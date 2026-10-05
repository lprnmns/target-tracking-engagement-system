from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from app.schemas.config import AppConfig
from app.schemas.decision import DecisionState, DecisionStateValue, FireEvaluationResult, GateStatus
from app.schemas.log import LogLevel
from app.schemas.vision import BalloonDetection, BodyDetection, VisionEvent
from app.schemas.tracking import LogicalTargetState
from app.services.log_service import JsonlLogService
from app.services.safety_gate_service import fail_gate, na_gate, pass_gate, warning_gate
from app.services.safety_timing import MAX_VISION_EVENT_AGE_S
from app.services.safety_zone_service import active_zone_name

if TYPE_CHECKING:
    from app.services.runtime_state import RuntimeState


class DecisionEngine:
    def __init__(self, config: AppConfig, logger: JsonlLogService) -> None:
        self.config = config
        self.logger = logger
        self.latest_decision: DecisionState | None = None
        self._observer_cache: DecisionState | None = None
        self._last_log_signature: tuple | None = None
        self._last_log_at = 0.0

    def evaluate_cached(self, runtime: "RuntimeState", max_age_s: float = 0.08) -> DecisionState:
        """Share one observer evaluation between simultaneous WebSocket tabs."""
        cached = self._observer_cache
        now = time.time()
        if cached is not None and 0.0 <= now - cached.updated_at <= max_age_s:
            return cached
        evaluated = self.evaluate(runtime)
        self._observer_cache = evaluated
        return evaluated

    def evaluate(self, runtime: "RuntimeState", operator_confirmed: bool = False) -> DecisionState:
        event = runtime.vision.latest_event
        system = runtime.system_state()
        now = time.time()
        vision_fresh = self._vision_event_is_fresh(event, now)
        body = self._select_body(event, runtime)
        logical_target = runtime.target_registry.target_for_body(body) if body is not None else None
        balloon = self._select_balloon(event, runtime, body, logical_target)
        person_safety = runtime.person_safety.evaluate(event)
        target_class = body.class_name if body else None
        target_team = self._team_for(body, runtime)
        range_m = body.range_m if body else None
        stable_frames = body.stable_frames if body else 0
        required_stable_frames = self.config.decision.stable_frames_required
        gates = []
        blocking: list[str] = []

        mode_reason = f"System is in {system.mode} {'dry-run' if system.dry_run else 'live'} mode."
        gates.append(
            fail_gate("system_disarmed_gate", mode_reason)
            if system.mode == "DISARMED"
            else pass_gate("system_disarmed_gate", mode_reason)
        )
        gates.append(
            pass_gate("system_armed_gate", "System is armed for the selected profile.")
            if system.armed
            else fail_gate("system_armed_gate", "System must be armed for the selected profile.")
        )
        gates.append(warning_gate("dry_run_gate", "Dry-run is enabled; physical fire is blocked.") if system.dry_run else pass_gate("dry_run_gate", "Dry-run disabled."))
        gates.append(pass_gate("hardware_enabled_gate", "Hardware enabled.") if system.hardware_enabled else fail_gate("hardware_enabled_gate", "Hardware is disabled; no physical command allowed."))

        serial_status = runtime.serial.status()
        gateway = runtime.command_gateway
        pico_connected = any(gate.code == "PICO_HANDSHAKE_OK" and gate.ready for gate in gateway.last_preflight.gates)
        estop_known = any(gate.code in {"ESTOP_RELEASED", "ESTOP_ACTIVE"} for gate in gateway.last_preflight.gates)
        gates.append(
            fail_gate("estop_gate", "Pico E-stop is active.")
            if gateway.pico_estop_active is True
            else pass_gate("estop_gate", "Pico E-stop is released.")
            if estop_known
            else fail_gate("estop_gate", "Pico E-stop state is unknown.")
        )
        gates.append(
            pass_gate("pico_connected_gate", "Pico handshake is current.")
            if pico_connected
            else fail_gate("pico_connected_gate", "Pico handshake is unavailable or failed.")
        )
        gates.append(
            pass_gate("pico_heartbeat_gate", "Pico heartbeat is within the configured timeout.")
            if gateway._heartbeat_is_fresh()
            else fail_gate("pico_heartbeat_gate", "Pico heartbeat is stale or unavailable.")
        )
        gates.append(pass_gate("serial_ok_gate", "Serial service mock state OK.") if serial_status.connection_state != "FAULT" else fail_gate("serial_ok_gate", serial_status.last_error or "Serial fault."))
        gates.extend(self._motion_gates(runtime))
        gates.append(pass_gate("vision_running_gate", "Vision pipeline running.") if runtime.vision.running else warning_gate("vision_running_gate", "Vision pipeline is not running; using latest/mock frame."))
        gates.append(
            pass_gate("vision_freshness_gate", "Latest vision event is within the safety freshness limit.")
            if vision_fresh
            else fail_gate(
                "vision_freshness_gate",
                f"Vision event is stale or missing; max age is {int(MAX_VISION_EVENT_AGE_S * 1000)}ms.",
            )
        )
        if runtime.mission.state.active_stage == "stage3":
            gates.extend(self._stage3_readiness_gates(runtime, body))
        gates.append(pass_gate("body_detected_gate", "Body detection selected.") if body else fail_gate("body_detected_gate", "No body detection."))
        gates.append(pass_gate("balloon_detected_gate", "Balloon detection selected.") if balloon else fail_gate("balloon_detected_gate", "Balloon not detected."))
        gates.append(self._logical_target_gate(body, logical_target))
        gates.append(pass_gate("team_classified_gate", f"Team classified as {target_team}.") if target_team != "unknown" else fail_gate("team_classified_gate", "Target team unknown.", critical=False))
        gates.append(pass_gate("enemy_target_gate", "Target classified as enemy.") if target_team == "enemy" else fail_gate("enemy_target_gate", "Target is not enemy."))
        gates.append(fail_gate("friend_rejection_gate", "NO_FIRE: target classified as friend.") if target_team == "friend" else pass_gate("friend_rejection_gate", "Target is not friend."))
        gates.append(
            fail_gate("person_safety_gate", "FIRE_BLOCKED: PERSON_DETECTED; no physical command generated.")
            if person_safety.person_detected
            else pass_gate("person_safety_gate", "No person/human class above configured confidence threshold.")
        )
        gates.append(
            self._range_gate(
                target_class,
                range_m,
                range_uncertainty_m=body.range_uncertainty_m if body else None,
                require_uncertainty=runtime.mission.state.active_stage == "stage3",
            )
        )
        gates.append(pass_gate("stable_track_gate", "Track stability requirement met.") if stable_frames >= required_stable_frames else fail_gate("stable_track_gate", "Track stability requirement not met.", critical=False))
        zone_name = active_zone_name(
            self.config.decision.fire_forbidden_zones,
            runtime.motion.status().pan_position_deg,
            runtime.motion.status().tilt_position_deg,
        )
        gates.append(
            na_gate("forbidden_zone_gate", "No fire-forbidden zone is configured.")
            if not self.config.decision.forbidden_zone_check_enabled
            else fail_gate("forbidden_zone_gate", f"NO_FIRE: turret is in forbidden fire zone '{zone_name}'.")
            if zone_name
            else pass_gate("forbidden_zone_gate", "Turret is outside configured fire-forbidden zones.")
        )
        gates.append(pass_gate("operator_confirm_gate", "Operator confirmed.") if operator_confirmed else fail_gate("operator_confirm_gate", "Operator confirmation required.", critical=False))

        for g in gates:
            if g.status == GateStatus.FAIL:
                blocking.append(self._blocking_reason(g.name, g.reason))

        decision_state = self._decision_state(body, balloon, target_team, range_m, stable_frames, required_stable_frames, blocking)
        reason = self._reason(decision_state, blocking)
        decision = DecisionState(
            decision_state=decision_state,
            active_target_id=body.id if body else None,
            logical_target_id=logical_target.target_id if logical_target is not None else None,
            logical_target_name=logical_target.display_name if logical_target is not None else None,
            logical_target_state=logical_target.state.value if logical_target is not None else None,
            logical_balloon_target_id=logical_target.balloon_target_id if logical_target is not None else None,
            selected_body_detection_id=body.id if body else None,
            selected_balloon_detection_id=balloon.id if balloon else None,
            target_class=target_class,
            target_team=target_team,
            range_m=range_m,
            stable_frames=stable_frames,
            required_stable_frames=required_stable_frames,
            gates=gates,
            blocking_reasons=blocking,
            decision_reason=reason,
            updated_at=now,
            aim_point={"x": balloon.center_x, "y": balloon.center_y} if balloon else None,
            person_safety=person_safety,
        )
        self.latest_decision = decision
        # The former 10 Hz *per browser tab* full-gate log grew beyond 1 GB
        # and competed with camera/serial work on Windows.  Preserve every
        # decision transition and a two-second heartbeat snapshot without
        # writing identical multi-kilobyte records for each socket.
        signature = (
            decision.decision_state,
            decision.active_target_id,
            decision.logical_target_id,
            tuple(decision.blocking_reasons),
            bool(person_safety.person_detected),
        )
        if signature != self._last_log_signature or now - self._last_log_at >= 2.0:
            self.logger.emit(LogLevel.INFO, "DECISION", "Decision evaluated", decision.model_dump(mode="json"))
            self._last_log_signature = signature
            self._last_log_at = now
        return decision

    def fire_request(self, runtime: "RuntimeState", operator_confirmed: bool) -> FireEvaluationResult:
        decision = self.evaluate(runtime, operator_confirmed=operator_confirmed)
        operation = getattr(runtime, "operation", None)
        is_manual_mode = operation is not None and operation.state().control_mode.value == "MANUAL"
        manual_operator_path = (
            is_manual_mode
            or operator_confirmed
            or runtime.command_gateway.profile.value in {"LIVE_TEST", "VIDEO_DEMO"}
            or (runtime.command_gateway.profile.value == "COMPETITION" and runtime.mission.state.active_stage == "stage1")
        )
        tracked_candidate = {"source": "stage1_manual_fire" if manual_operator_path else "api_fire_request", "operator_confirmed": operator_confirmed}
        if decision.selected_balloon_detection_id is not None:
            track = next(
                (
                    item
                    for item in runtime.auto_tracker.status().multi_target_tracker.tracks
                    if item.detection_id == decision.selected_balloon_detection_id and item.fresh
                ),
                None,
            )
            if track is not None:
                tracked_candidate["balloon_track_id"] = track.track_id
                tracked_candidate["balloon_detection_id"] = track.detection_id
        if decision.selected_body_detection_id is not None:
            tracked_candidate["body_detection_id"] = decision.selected_body_detection_id
        if decision.logical_target_id is not None:
            tracked_candidate["logical_target_id"] = decision.logical_target_id
            tracked_candidate["logical_target_state"] = decision.logical_target_state
        gateway_result = (
            runtime.command_gateway.fire_from_operator(runtime, tracked_candidate)
            if manual_operator_path
            else runtime.command_gateway.fire_from_tracking(runtime, tracked_candidate)
        )
        accepted = gateway_result.accepted
        reason = gateway_result.detail
        blocking = list(dict.fromkeys(gateway_result.reason_codes if manual_operator_path else [*decision.blocking_reasons, *gateway_result.reason_codes]))
        self.logger.emit(
            LogLevel.INFO if accepted else LogLevel.WARN,
            "SAFETY",
            "Fire request rejected by CommandGateway" if not accepted else "Fire request acknowledged by CommandGateway",
            {"accepted": accepted, "reason": reason, "gateway": gateway_result.model_dump(mode="json"), "decision": decision.model_dump(mode="json")},
        )
        return FireEvaluationResult(
            accepted=accepted,
            dry_run=runtime.config.system.dry_run,
            decision_state=decision.decision_state,
            blocking_reasons=blocking,
            gates=decision.gates,
            reason=reason,
        )

    def _select_body(self, event: VisionEvent | None, runtime: "RuntimeState" | None = None) -> BodyDetection | None:
        if not event or not event.body_detections:
            return None
        if runtime is not None:
            registry = getattr(runtime, "target_registry", None)
            if registry is not None:
                linked = [
                    body
                    for body in event.body_detections
                    if (target := registry.target_for_body(body)) is not None
                    and target.state != LogicalTargetState.DESTROYED
                ]
                if linked:
                    return max(linked, key=lambda item: item.confidence)
        return max(event.body_detections, key=lambda item: item.confidence)

    def _select_balloon(
        self,
        event: VisionEvent | None,
        runtime: "RuntimeState" | None = None,
        body: BodyDetection | None = None,
        logical_target=None,
    ) -> BalloonDetection | None:
        if not event or not event.balloon_detections:
            return None
        if runtime is not None and body is not None:
            registry = getattr(runtime, "target_registry", None)
            if registry is not None:
                target = logical_target or registry.target_for_body(body)
                if target is not None and target.state == LogicalTargetState.DESTROYED:
                    return None
                if target is not None and target.body_track_id is not None:
                    track = next(
                        (
                            item
                            for item in runtime.auto_tracker.status().multi_target_tracker.tracks
                            if item.track_id == target.balloon_track_id and item.fresh
                        ),
                        None,
                    )
                    if track is None:
                        return None
                    selected = next((item for item in event.balloon_detections if item.id == track.detection_id), None)
                    return selected
        threshold = self.config.vision.balloon_conf_threshold
        candidates = [item for item in event.balloon_detections if item.confidence >= threshold]
        return max(candidates, key=lambda item: item.confidence) if candidates else None

    def _logical_target_gate(self, body: BodyDetection | None, logical_target):
        if body is None:
            return na_gate("target_identity_gate", "No body target; standalone balloon identity is used.")
        # Only the four competition aircraft classes participate in the
        # aircraft→balloon identity contract.  Person/other safety detections
        # must not accidentally claim a balloon.
        if not self._is_aircraft_body(body):
            return na_gate("target_identity_gate", "Non-aircraft body; aircraft balloon identity is not applicable.")
        if logical_target is None:
            return fail_gate("target_identity_gate", "Aircraft-to-balloon identity is not established.", critical=False)
        if logical_target.state == LogicalTargetState.DESTROYED:
            return fail_gate("target_identity_gate", "Target balloon is confirmed destroyed; no re-targeting allowed.")
        if logical_target.body_track_id is not None and logical_target.state != LogicalTargetState.READY:
            return fail_gate(
                "target_identity_gate",
                f"Aircraft balloon association needs {max(0.0, 1.0 - logical_target.association_duration_s):.2f}s more temporal evidence.",
                critical=False,
            )
        return pass_gate("target_identity_gate", f"Logical target {logical_target.target_id} owns one balloon.")

    @staticmethod
    def _is_aircraft_body(body: BodyDetection | None) -> bool:
        if body is None:
            return False
        token = (body.class_name or "").strip().lower().replace("-", "_")
        return token in {"f16", "f_16", "enemy_f16", "f16_enemy", "helicopter", "enemy_helicopter", "helicopter_enemy", "ballistic_missile", "enemy_ballistic_missile", "mini_micro_uav", "enemy_mini_micro_uav"}

    def _team_for(self, body: BodyDetection | None, runtime: "RuntimeState") -> str:
        if body is None:
            return "unknown"
        color_result = runtime.color_classifier.latest_for_body(body)
        current_frame_id = runtime.vision.latest_event.frame_id if runtime.vision.latest_event else None
        # A mock/sample endpoint is an engineering tool, not IFF evidence.
        # Field rule: a positive friend-colour match is FRIEND; every other
        # aircraft is ENEMY. Temporal ROI evidence remains telemetry, not a
        # third operational team that stalls autonomous acquisition.
        if color_result and color_result.evidence_source == "real_body_roi":
            if color_result.decision.value == "friend":
                return "friend"
            return "enemy"
        # The legacy sample classifier remains useful for dry-run/Stage-1
        # decision visualisation.  It is explicitly excluded from A3, where
        # a physical candidate requires frame-derived temporal ROI evidence.
        if color_result and runtime.mission.state.active_stage != "stage3":
            return color_result.decision.value
        if body.target_team == "friend":
            return "friend"
        if body.target_team in {"enemy", "unknown"}:
            return "enemy"
        if body.color_hint and body.color_hint.startswith("enemy"):
            return "enemy"
        if body.color_hint and body.color_hint.startswith("friend"):
            return "friend"
        return "enemy"

    def _range_gate(
        self,
        target_class: str | None,
        range_m: float | None,
        range_uncertainty_m: float | None = None,
        require_uncertainty: bool = False,
    ):
        if target_class is None:
            return fail_gate("range_valid_gate", "No target class for range validation.")
        rule = self.config.decision.range_rules.get(target_class)
        if rule is None:
            return fail_gate("range_valid_gate", f"No range rule for {target_class}; fire forbidden.")
        if range_m is None:
            return pass_gate("range_valid_gate", f"{target_class} range uncalibrated; range gate bypassed.")
        if require_uncertainty:
            if range_uncertainty_m is None or range_uncertainty_m < 0:
                return pass_gate("range_valid_gate", "A3 metric range uncertainty uncalibrated; range gate bypassed.")
            lower = range_m - range_uncertainty_m
            upper = range_m + range_uncertainty_m
            if lower < rule.min_m or upper > rule.max_m:
                return fail_gate(
                    "range_valid_gate",
                    f"{target_class} range interval {lower:.1f}-{upper:.1f}m is not fully inside {rule.min_m}-{rule.max_m}m.",
                )
        if rule.min_m <= range_m <= rule.max_m:
            return pass_gate("range_valid_gate", f"{target_class} range {range_m:.1f}m is valid.")
        return fail_gate("range_valid_gate", f"{target_class} range {range_m:.1f}m outside {rule.min_m}-{rule.max_m}m.")

    def _decision_state(
        self,
        body: BodyDetection | None,
        balloon: BalloonDetection | None,
        team: str,
        range_m: float | None,
        stable_frames: int,
        required_stable_frames: int,
        blocking: list[str],
    ) -> DecisionStateValue:
        if body is None:
            return DecisionStateValue.NO_TARGET
        if "PERSON_DETECTED" in blocking:
            return DecisionStateValue.NO_FIRE
        if team == "friend":
            return DecisionStateValue.NO_FIRE
        if "target_already_destroyed" in blocking:
            return DecisionStateValue.NO_FIRE
        if "target_balloon_association_pending" in blocking or "target_identity_unresolved" in blocking:
            return DecisionStateValue.WAIT
        if team == "unknown":
            return DecisionStateValue.WAIT
        if balloon is None:
            return DecisionStateValue.WAIT
        if stable_frames < required_stable_frames:
            return DecisionStateValue.WAIT
        hard_blocks = {"target_is_friend", "range_invalid", "hardware_disabled", "system_disarmed", "vision_stale"}
        if any(reason in hard_blocks for reason in blocking):
            return DecisionStateValue.NO_FIRE
        if blocking:
            return DecisionStateValue.LOCKED
        return DecisionStateValue.FIRE_READY

    def _blocking_reason(self, gate_name: str, reason: str) -> str:
        if gate_name == "estop_gate":
            return "ESTOP_STATE_UNKNOWN" if "unknown" in reason.lower() else "ESTOP_ACTIVE"
        mapping = {
            "system_disarmed_gate": "system_disarmed",
            "system_armed_gate": "system_disarmed",
            "hardware_enabled_gate": "hardware_disabled",
            "body_detected_gate": "body_not_detected",
            "balloon_detected_gate": "balloon_not_detected",
            "target_identity_gate": "target_already_destroyed" if "destroyed" in reason.lower() else "target_balloon_association_pending",
            "team_classified_gate": "team_unknown",
            "enemy_target_gate": "target_not_enemy",
            "friend_rejection_gate": "target_is_friend",
            "person_safety_gate": "PERSON_DETECTED",
            "range_valid_gate": "range_invalid",
            "stable_track_gate": "track_not_stable",
            "operator_confirm_gate": "operator_confirmation_missing",
            "serial_ok_gate": "serial_fault",
            "pico_connected_gate": "PICO_HANDSHAKE_FAILED",
            "pico_heartbeat_gate": "PICO_HEARTBEAT_STALE",
            "motion_soft_limits_gate": "motion_soft_limit_fault",
            "motion_estop_gate": "motion_estop_active",
            "motion_fault_gate": "motion_fault",
            "motion_driver_gate": "motion_driver_disabled",
            "motion_dry_run_gate": "motion_dry_run",
            "vision_freshness_gate": "vision_stale",
            "forbidden_zone_gate": "fire_forbidden_zone",
            "a3_body_model_gate": "a3_body_model_missing_or_unverified",
            "a3_iff_real_roi_gate": "a3_iff_real_roi_unavailable",
            "a3_range_calibration_gate": "a3_range_calibration_unavailable",
        }
        return mapping.get(gate_name, reason.lower().replace(" ", "_"))

    def _reason(self, state: DecisionStateValue, blocking: list[str]) -> str:
        if "target_is_friend" in blocking:
            return "NO_FIRE: target classified as friend."
        if "PERSON_DETECTED" in blocking:
            return "FIRE_BLOCKED: PERSON_DETECTED; additional software safety gate active."
        if "vision_stale" in blocking:
            return "NO_FIRE: latest vision event is stale or missing."
        if "target_already_destroyed" in blocking:
            return "NO_FIRE: target balloon is confirmed destroyed; retargeting is disabled."
        if "target_balloon_association_pending" in blocking:
            return "WAIT: aircraft-to-balloon identity requires one second of stable evidence."
        if state == DecisionStateValue.NO_TARGET:
            return "No target available."
        if blocking:
            return f"{state}: blocked by {blocking[0]}."
        return f"{state}: all decision gates passed for dry-run evaluation."

    @staticmethod
    def _vision_event_is_fresh(event: VisionEvent | None, now: float) -> bool:
        if event is None:
            return False
        age_s = now - float(event.timestamp_ms) / 1000.0
        return -1.0 <= age_s <= MAX_VISION_EVENT_AGE_S

    def _motion_gates(self, runtime: "RuntimeState"):
        state = runtime.motion.status()
        settings = runtime.motion.settings
        soft_limits_ok = (
            settings.pan_min_deg <= state.pan_position_deg <= settings.pan_max_deg
            and settings.tilt_min_deg <= state.tilt_position_deg <= settings.tilt_max_deg
        )
        gates = [
            pass_gate("motion_soft_limits_gate", "Motion position is inside configured soft limits.")
            if soft_limits_ok
            else fail_gate("motion_soft_limits_gate", "Motion position is outside configured soft limits."),
            pass_gate("motion_estop_gate", "Motion E-stop is not active.")
            if not state.estop_state
            else fail_gate("motion_estop_gate", "Motion E-stop is active."),
            pass_gate("motion_fault_gate", "Motion service has no fault.")
            if state.motion_state != "FAULT"
            else fail_gate("motion_fault_gate", state.last_error or "Motion service fault."),
            warning_gate("motion_driver_gate", "Driver disabled in Phase 7 dry-run mode.")
            if not state.driver_enabled
            else pass_gate("motion_driver_gate", "Motion driver enabled."),
            warning_gate("motion_dry_run_gate", "Motion dry-run is enabled; no physical movement is generated.")
            if state.dry_run
            else pass_gate("motion_dry_run_gate", "Motion physical-output profile is enabled."),
        ]
        return gates

    def _stage3_readiness_gates(self, runtime: "RuntimeState", body: BodyDetection | None):
        profile = runtime.vision_runtime.profile
        body_model_id = profile.active_body_model_id
        body_model_ok = False
        body_detail = "No active body model is selected."
        if body_model_id:
            try:
                model = runtime.model_registry.get_model(body_model_id)
                required = {"f16", "helicopter", "ballistic_missile", "mini_micro_uav"}
                class_names = {name.lower() for name in model.class_names}
                body_model_ok = bool(model.file_path and Path(model.file_path).exists() and required <= class_names)
                body_detail = "Active body model has verified required class metadata." if body_model_ok else "Body model file or required class metadata is missing."
                try:
                    package = runtime.model_packages.get_package(body_model_id)
                    semantic = runtime.model_packages.semantic_state(package)
                    if not semantic.competition_ready:
                        body_model_ok = False
                        body_detail = "Body model package lacks verified real tensor/golden inference evidence."
                except KeyError:
                    body_model_ok = False
                    body_detail = "Body model is not backed by a verified model package."
            except KeyError:
                body_detail = "Selected body model is absent from the registry."
        event = runtime.vision.latest_event
        iff_ready, iff_detail = runtime.color_classifier.real_iff_ready_for(body, event.frame_id if event else None)
        body_model_path = None
        if body_model_id:
            try:
                body_model_path = runtime.model_registry.get_model(body_model_id).file_path
            except KeyError:
                body_model_path = None
        range_ready, range_detail = runtime.stage3_range.ready_for(body, body_model_id, body_model_path)
        return [
            pass_gate("a3_body_model_gate", body_detail) if body_model_ok else fail_gate("a3_body_model_gate", body_detail),
            pass_gate("a3_iff_real_roi_gate", iff_detail) if iff_ready else fail_gate("a3_iff_real_roi_gate", iff_detail),
            pass_gate("a3_range_calibration_gate", range_detail) if range_ready else fail_gate("a3_range_calibration_gate", range_detail),
        ]
