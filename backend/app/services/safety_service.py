from app.schemas.config import AppConfig
from app.schemas.decision import DecisionState
from app.schemas.log import LogLevel
from app.schemas.safety import DecisionStatus, SafetyCommandResult, SafetyGateState, SafetyState
from app.services.log_service import JsonlLogService


class SafetyService:
    def __init__(self, config: AppConfig, logger: JsonlLogService) -> None:
        self.config = config
        self.logger = logger

    def state(self, decision: DecisionState | None = None) -> SafetyState:
        if decision is None:
            return SafetyState()
        gate_status = {gate.name: gate.status for gate in decision.gates}
        return SafetyState(
            decision=DecisionStatus.FIRE_READY if decision.decision_state == "FIRE_READY" else DecisionStatus.NO_FIRE,
            gates=SafetyGateState(
                armed=gate_status.get("system_armed_gate") == "pass",
                estop_released=gate_status.get("estop_gate") == "pass",
                pico_heartbeat=gate_status.get("pico_heartbeat_gate") in {"pass", "warning"},
                track_stable=gate_status.get("stable_track_gate") == "pass",
                target_enemy=gate_status.get("enemy_target_gate") == "pass",
                balloon_detected=gate_status.get("balloon_detected_gate") == "pass",
                range_valid=gate_status.get("range_valid_gate") == "pass",
                aim_point_valid=decision.aim_point is not None,
                zone_valid=gate_status.get("forbidden_zone_gate") in {"pass", "not_applicable"},
                operator_or_auto_permission=gate_status.get("operator_confirm_gate") == "pass",
                hardware_enabled=gate_status.get("hardware_enabled_gate") == "pass",
                dry_run=True,
                motion_soft_limits=gate_status.get("motion_soft_limits_gate") == "pass",
                motion_estop=gate_status.get("motion_estop_gate") == "pass",
                motion_fault_clear=gate_status.get("motion_fault_gate") == "pass",
                motion_driver=gate_status.get("motion_driver_gate") == "pass",
                motion_dry_run=gate_status.get("motion_dry_run_gate") in {"pass", "warning"},
                person_safety_clear=gate_status.get("person_safety_gate") == "pass",
            ),
            reason=decision.decision_reason,
            blocking_reasons=decision.blocking_reasons,
        )

    def reject_command(self, command: str) -> SafetyCommandResult:
        state = self.state()
        result = SafetyCommandResult(
            accepted=False,
            command=command,
            decision=DecisionStatus.NO_FIRE,
            reason=f"{command} rejected: system is DISARMED, NO_FIRE, dry-run and hardware disabled.",
            blocking_reasons=state.blocking_reasons,
        )
        self.logger.emit(
            LogLevel.WARN,
            "SAFETY",
            "Command rejected by default safety policy",
            result.model_dump(mode="json"),
        )
        return result
