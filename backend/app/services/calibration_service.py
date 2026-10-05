import math
import time
import uuid
import json
import hashlib

from app.schemas.calibration import (
    CalibrationComputeResult,
    CalibrationConfigModel,
    CalibrationPoint,
    CalibrationPointCreate,
    CalibrationStatus,
    CalibrationStatusValue,
    CameraCalibrationConfig,
    DirectionCalibrationProfile,
    DirectionCalibrationStatus,
    DirectionMotion,
    DirectionObservationRequest,
    DirectionObservationResult,
    DirectionSimulationRequest,
    DirectionSimulationResult,
    FovEstimateRequest,
    FovEstimateResponse,
    SimulatedAxis,
    WarningLevel,
)
from app.schemas.config import AppConfig
from app.schemas.log import LogLevel
from app.services.log_service import JsonlLogService
from app.services.storage_paths import project_root

try:  # pragma: no cover - deployment image dependent
    import cv2
    import numpy as np
except Exception:  # pragma: no cover
    cv2 = None
    np = None


MAX_REPROJECTION_ERROR_PX = 3.0


class CalibrationService:
    def __init__(self, config: AppConfig, logger: JsonlLogService) -> None:
        self.config = config
        self.logger = logger
        now = time.time()
        self.profile = CameraCalibrationConfig(
            camera_id="mock_camera_0",
            camera_name="Mock Camera",
            lens_profile=config.calibration.lens_profile,
            resolution_width=config.calibration.resolution_width,
            resolution_height=config.calibration.resolution_height,
            fps=config.calibration.fps,
            camera_height_cm=config.calibration.camera_height_cm,
            target_height_cm=config.calibration.target_height_cm,
            table_height_cm=config.calibration.table_height_cm,
            hfov_deg=config.calibration.hfov_deg,
            vfov_deg=config.calibration.vfov_deg,
            distortion_enabled=config.calibration.distortion_enabled,
            homography_enabled=config.calibration.homography_enabled,
            calibration_status=CalibrationStatusValue.NOT_STARTED,
            updated_at=now,
        )
        self.points: list[CalibrationPoint] = []
        self.homography_matrix: list[list[float]] | None = None
        self.reprojection_error_px: float | None = None
        self.inlier_count = 0
        self.calibration_hash: str | None = None
        self.warnings: list[str] = ["field_calibration_required"]
        self.last_event: tuple[str, dict] | None = None
        self.direction_profile = self._default_direction_profile()
        self.direction_observations: list[DirectionObservationResult] = []
        self.latest_direction_simulation: DirectionSimulationResult | None = None
        self.direction_profile_path = project_root() / "config" / "runtime" / "direction_calibration_profile.active.json"

    def status(self) -> CalibrationStatus:
        return CalibrationStatus(
            config=self.profile,
            calibration_points=self.points,
            homography_matrix=self.homography_matrix,
            reprojection_error_px=self.reprojection_error_px,
            inlier_count=self.inlier_count,
            calibration_hash=self.calibration_hash,
            valid=self.profile.calibration_status == CalibrationStatusValue.VALID,
            warnings=self.warnings,
            updated_at=self.profile.updated_at,
        )

    def config_model(self) -> CameraCalibrationConfig:
        return self.profile

    def update_config(self, update: CalibrationConfigModel) -> CameraCalibrationConfig:
        data = update.model_dump()
        data["updated_at"] = time.time()
        self.profile = CameraCalibrationConfig(**data)
        self.profile.calibration_status = CalibrationStatusValue.PARTIAL if self.points else CalibrationStatusValue.NOT_STARTED
        self._invalidate_solution()
        self.warnings = ["field_calibration_required"]
        payload = self.status().model_dump(mode="json")
        self.last_event = ("calibration.updated", payload)
        self.logger.emit(LogLevel.INFO, "CALIBRATION", "Calibration config updated", payload)
        return self.profile

    def add_point(self, create: CalibrationPointCreate) -> CalibrationStatus:
        point = CalibrationPoint(id=str(uuid.uuid4()), **create.model_dump())
        self.points.append(point)
        self.profile.calibration_status = CalibrationStatusValue.PARTIAL
        self._invalidate_solution()
        self.profile.updated_at = time.time()
        payload = self.status().model_dump(mode="json")
        self.last_event = ("calibration.updated", payload)
        self.logger.emit(LogLevel.INFO, "CALIBRATION", "Calibration point added", point.model_dump(mode="json"))
        return self.status()

    def delete_point(self, point_id: str) -> CalibrationStatus:
        self.points = [point for point in self.points if point.id != point_id]
        self.profile.calibration_status = CalibrationStatusValue.PARTIAL if self.points else CalibrationStatusValue.NOT_STARTED
        self._invalidate_solution()
        self.profile.updated_at = time.time()
        payload = self.status().model_dump(mode="json")
        self.last_event = ("calibration.updated", payload)
        self.logger.emit(LogLevel.INFO, "CALIBRATION", "Calibration point deleted", {"id": point_id})
        return self.status()

    def compute(self) -> CalibrationComputeResult:
        warnings: list[str] = []
        valid = len(self.points) >= 4
        self._invalidate_solution()
        if not valid:
            warnings.append("at_least_4_points_required_for_homography")
        if not self.profile.homography_enabled:
            warnings.append("homography_disabled")
        if valid and self.profile.homography_enabled:
            valid, homography_warnings = self._compute_homography()
            warnings.extend(homography_warnings)
        self.warnings = warnings or ["field_calibration_required"]
        self.profile.calibration_status = CalibrationStatusValue.VALID if valid else CalibrationStatusValue.INVALID
        self.profile.updated_at = time.time()
        result = CalibrationComputeResult(
            calibration_points=self.points,
            homography_matrix=self.homography_matrix,
            reprojection_error_px=self.reprojection_error_px,
            inlier_count=self.inlier_count,
            calibration_hash=self.calibration_hash,
            valid=valid,
            warnings=self.warnings,
            updated_at=self.profile.updated_at,
        )
        payload = result.model_dump(mode="json")
        event_type = "calibration.updated" if valid else "calibration.warning"
        self.last_event = (event_type, payload)
        self.logger.emit(LogLevel.INFO if valid else LogLevel.WARN, "CALIBRATION", "Calibration compute evaluated", payload)
        return result

    def fov_estimate(self, request: FovEstimateRequest) -> FovEstimateResponse:
        visible_width_m = 2 * request.distance_m * math.tan(math.radians(request.hfov_deg) / 2)
        object_width_px = request.object_width_m / visible_width_m * request.image_width_px
        if object_width_px >= 120:
            level = WarningLevel.GOOD
        elif object_width_px >= 60:
            level = WarningLevel.MARGINAL
        else:
            level = WarningLevel.POOR
        response = FovEstimateResponse(
            visible_width_m=round(visible_width_m, 4),
            object_width_px=round(object_width_px, 2),
            warning_level=level,
        )
        self.logger.emit(
            LogLevel.INFO,
            "CALIBRATION",
            "FOV estimate evaluated",
            {"request": request.model_dump(mode="json"), "response": response.model_dump(mode="json")},
        )
        return response

    def reset(self) -> CalibrationStatus:
        self.points = []
        self._invalidate_solution()
        self.warnings = ["field_calibration_required"]
        self.profile.calibration_status = CalibrationStatusValue.NOT_STARTED
        self.profile.updated_at = time.time()
        payload = self.status().model_dump(mode="json")
        self.last_event = ("calibration.updated", payload)
        self.logger.emit(LogLevel.INFO, "CALIBRATION", "Calibration reset", payload)
        return self.status()

    def _compute_homography(self) -> tuple[bool, list[str]]:
        if cv2 is None or np is None:
            return False, ["homography_runtime_dependency_unavailable"]
        world = np.asarray([[point.world_x_m, point.world_y_m] for point in self.points], dtype=np.float32)
        image = np.asarray([[point.image_x_px, point.image_y_px] for point in self.points], dtype=np.float32)
        if len(np.unique(world, axis=0)) < 4 or len(np.unique(image, axis=0)) < 4:
            return False, ["homography_duplicate_points"]
        if np.linalg.matrix_rank(world - world.mean(axis=0)) < 2 or np.linalg.matrix_rank(image - image.mean(axis=0)) < 2:
            return False, ["homography_degenerate_points"]
        matrix, mask = cv2.findHomography(world, image, method=cv2.RANSAC, ransacReprojThreshold=MAX_REPROJECTION_ERROR_PX)
        if matrix is None or not np.isfinite(matrix).all():
            return False, ["homography_compute_failed"]
        projected = cv2.perspectiveTransform(world.reshape(-1, 1, 2), matrix).reshape(-1, 2)
        errors = np.linalg.norm(projected - image, axis=1)
        inlier_mask = mask.reshape(-1).astype(bool) if mask is not None else np.ones(len(world), dtype=bool)
        inlier_count = int(inlier_mask.sum())
        if inlier_count < 4:
            return False, ["homography_insufficient_inliers"]
        rms = float(np.sqrt(np.mean(np.square(errors[inlier_mask]))))
        if not math.isfinite(rms) or rms > MAX_REPROJECTION_ERROR_PX:
            return False, ["homography_reprojection_error_too_high"]
        self.homography_matrix = [[round(float(item), 10) for item in row] for row in matrix.tolist()]
        self.reprojection_error_px = round(rms, 5)
        self.inlier_count = inlier_count
        hash_payload = {
            "config": self.profile.model_dump(mode="json", exclude={"calibration_status", "updated_at"}),
            "points": [point.model_dump(mode="json") for point in self.points],
            "matrix": self.homography_matrix,
        }
        self.calibration_hash = hashlib.sha256(json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return True, []

    def _invalidate_solution(self) -> None:
        self.homography_matrix = None
        self.reprojection_error_px = None
        self.inlier_count = 0
        self.calibration_hash = None

    def direction_status(self) -> DirectionCalibrationStatus:
        return DirectionCalibrationStatus(
            profile=self.direction_profile,
            latest_simulation=self.latest_direction_simulation,
            latest_observation=self.direction_observations[-1] if self.direction_observations else None,
            observation_count=len(self.direction_observations),
            advisory_only=True,
            physical_command_enabled=False,
            no_physical_command_generated=True,
        )

    def direction_latest(self) -> DirectionCalibrationProfile:
        return self.direction_profile

    def direction_simulate(self, request: DirectionSimulationRequest) -> DirectionSimulationResult:
        frame_center_x = request.frame_width / 2
        frame_center_y = request.frame_height / 2
        offset_x = request.frame_width * 0.25
        offset_y = request.frame_height * 0.25
        target_x = request.target_center_x
        target_y = request.target_center_y
        if target_x is None or target_y is None:
            target_x = frame_center_x
            target_y = frame_center_y
            if request.target_position == "left":
                target_x = frame_center_x - offset_x
            elif request.target_position == "right":
                target_x = frame_center_x + offset_x
            elif request.target_position == "up":
                target_y = frame_center_y - offset_y
            elif request.target_position == "down":
                target_y = frame_center_y + offset_y
        error_x = float(target_x - frame_center_x)
        error_y = float(target_y - frame_center_y)
        visual_side = self._visual_side(error_x, error_y)
        required_motion = self._required_motion(error_x, error_y)
        result = DirectionSimulationResult(
            target_visual_side=visual_side,
            target_error_x=round(error_x, 3),
            target_error_y=round(error_y, 3),
            required_camera_motion=required_motion,
            expected_image_response=self._expected_response(required_motion),
            frame_center_x=round(frame_center_x, 3),
            frame_center_y=round(frame_center_y, 3),
            target_center_x=round(float(target_x), 3),
            target_center_y=round(float(target_y), 3),
            advisory_motion_only=True,
            physical_command_enabled=False,
            no_physical_command_generated=True,
        )
        self.latest_direction_simulation = result
        payload = result.model_dump(mode="json")
        summary = f"Direction semantics simulated; target={visual_side}; required_motion={required_motion}; no_physical_command_generated=true."
        self._direction_event("calibration.direction_simulated", payload, summary)
        return result

    def direction_record_observation(self, request: DirectionObservationRequest) -> DirectionObservationResult:
        x_multiplier = self.direction_profile.x_axis_multiplier
        y_multiplier = self.direction_profile.y_axis_multiplier
        axis_swap = self.direction_profile.axis_swap
        if request.simulated_axis == SimulatedAxis.X:
            if request.operator_observed_motion in {DirectionMotion.CAMERA_UP, DirectionMotion.CAMERA_DOWN}:
                axis_swap = True
            elif request.operator_observed_motion == request.system_expected_motion:
                x_multiplier = 1
            elif request.operator_observed_motion in {DirectionMotion.CAMERA_LEFT, DirectionMotion.CAMERA_RIGHT}:
                x_multiplier = -1
        if request.simulated_axis == SimulatedAxis.Y:
            if request.operator_observed_motion in {DirectionMotion.CAMERA_LEFT, DirectionMotion.CAMERA_RIGHT}:
                axis_swap = True
            elif request.operator_observed_motion == request.system_expected_motion:
                y_multiplier = 1
            elif request.operator_observed_motion in {DirectionMotion.CAMERA_UP, DirectionMotion.CAMERA_DOWN}:
                y_multiplier = -1
        result = DirectionObservationResult(
            observation_id=f"direction_observation_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
            simulated_axis=request.simulated_axis,
            system_expected_motion=request.system_expected_motion,
            operator_observed_motion=request.operator_observed_motion,
            operator_confidence=request.operator_confidence,
            suggested_x_axis_multiplier=x_multiplier,
            suggested_y_axis_multiplier=y_multiplier,
            axis_swap_suspected=axis_swap,
            confidence=request.operator_confidence.value,
            note=request.note,
            advisory_only=True,
            physical_command_enabled=False,
            no_physical_command_generated=True,
        )
        self.direction_observations.append(result)
        payload = result.model_dump(mode="json")
        summary = f"Direction observation recorded; axis={request.simulated_axis}; axis_swap_suspected={axis_swap}; no_physical_command_generated=true."
        self._direction_event("calibration.direction_observation_recorded", payload, summary)
        return result

    def direction_save_profile(self) -> DirectionCalibrationProfile:
        latest = self.direction_observations[-1] if self.direction_observations else None
        update = {
            "profile_id": f"direction_profile_{time.strftime('%Y%m%d_%H%M%S')}",
            "updated_at": time.time(),
            "source": "operator_observation" if latest else "manual_simulation",
            "x_axis_multiplier": latest.suggested_x_axis_multiplier if latest else self.direction_profile.x_axis_multiplier,
            "y_axis_multiplier": latest.suggested_y_axis_multiplier if latest else self.direction_profile.y_axis_multiplier,
            "axis_swap": latest.axis_swap_suspected if latest else self.direction_profile.axis_swap,
            "notes": latest.note if latest and latest.note else self.direction_profile.notes,
        }
        if self.direction_profile.created_at <= 0:
            update["created_at"] = time.time()
        self.direction_profile = self.direction_profile.model_copy(update=update)
        self.direction_profile_path.parent.mkdir(parents=True, exist_ok=True)
        self.direction_profile_path.write_text(json.dumps(self.direction_profile.model_dump(mode="json"), indent=2), encoding="utf-8")
        payload = self.direction_profile.model_dump(mode="json")
        summary = "Direction calibration profile saved; no_physical_command_generated=true."
        self._direction_event("calibration.direction_profile_saved", payload, summary)
        return self.direction_profile

    def direction_reset(self) -> DirectionCalibrationStatus:
        self.direction_profile = self._default_direction_profile()
        self.direction_observations = []
        self.latest_direction_simulation = None
        payload = self.direction_status().model_dump(mode="json")
        summary = "Direction calibration profile reset; no_physical_command_generated=true."
        self._direction_event("calibration.direction_profile_reset", payload, summary)
        return self.direction_status()

    def direction_profile_json(self) -> str:
        return json.dumps(self.direction_profile.model_dump(mode="json"), indent=2)

    def direction_observation_log_json(self) -> str:
        return json.dumps(
            {
                "observations": [item.model_dump(mode="json") for item in self.direction_observations],
                "advisory_only": True,
                "physical_command_enabled": False,
                "no_physical_command_generated": True,
            },
            indent=2,
        )

    def direction_simulation_summary_markdown(self) -> str:
        latest = self.latest_direction_simulation
        latest_text = "- Latest simulation: not_run"
        if latest:
            latest_text = (
                f"- Target visual side: {latest.target_visual_side}\n"
                f"- Target error x/y: {latest.target_error_x} / {latest.target_error_y}\n"
                f"- Required camera motion: {latest.required_camera_motion}\n"
                f"- Expected image response: {latest.expected_image_response}"
            )
        return f"""# Direction Simulation Summary

{latest_text}

- advisory_only=true
- physical_command_enabled=false
- no_physical_command_generated=true

This simulation defines screen/image direction semantics and expected camera response. It does not send motor, serial, GPIO, PWM, STEP/DIR or hardware-enable commands.
"""

    def motion_semantics_contract_markdown(self) -> str:
        return """# Motion Semantics Contract

## Screen/Image Semantics

- image_x_positive = right
- image_y_positive = down
- target_error_x = target_center_x - frame_center_x
- target_error_y = target_center_y - frame_center_y

## Required Camera Motion

- Target right -> pan_right
- Target left -> pan_left
- Target up -> tilt_up
- Target down -> tilt_down

## Expected Image Response

- Camera pan_right -> target moves left toward center
- Camera pan_left -> target moves right toward center
- Camera tilt_up -> target moves down toward center
- Camera tilt_down -> target moves up toward center

## Safety Boundary

- advisory_only=true
- physical_command_enabled=false
- no_physical_command_generated=true
- No motor, servo, GPIO, PWM, STEP/DIR, TMC current, serial write, fire or hardware enable path is activated.
"""

    def direction_safety_boundary_markdown(self) -> str:
        return """# Direction Calibration Safety Boundary

This phase is a simulator and operator-observation evidence layer only.

- No motor command was sent.
- No serial write was performed.
- No GPIO/PWM/STEP/DIR path enabled.
- physical_command_enabled=false
- no_physical_command_generated=true
- Safety invariant: DISARMED + NO_FIRE + dry_run=true + hardware_enabled=false + physical_command_enabled=false
"""

    def _default_direction_profile(self) -> DirectionCalibrationProfile:
        now = time.time()
        return DirectionCalibrationProfile(created_at=now, updated_at=now)

    def _visual_side(self, error_x: float, error_y: float) -> str:
        if abs(error_x) < 1 and abs(error_y) < 1:
            return "center"
        if abs(error_x) >= abs(error_y):
            return "right" if error_x > 0 else "left"
        return "down" if error_y > 0 else "up"

    def _required_motion(self, error_x: float, error_y: float) -> str:
        side = self._visual_side(error_x, error_y)
        return {
            "right": "pan_right",
            "left": "pan_left",
            "up": "tilt_up",
            "down": "tilt_down",
            "center": "none",
        }[side]

    def _expected_response(self, motion: str) -> str:
        return {
            "pan_right": "target_should_move_left_toward_center",
            "pan_left": "target_should_move_right_toward_center",
            "tilt_up": "target_should_move_down_toward_center",
            "tilt_down": "target_should_move_up_toward_center",
            "none": "target_already_centered",
        }[motion]

    def _direction_event(self, event_type: str, payload: dict, message: str) -> None:
        safe_payload = {
            **payload,
            "advisory_only": True,
            "physical_command_enabled": False,
            "no_physical_command_generated": True,
        }
        self.last_event = (event_type, safe_payload)
        self.logger.emit(LogLevel.INFO, "CALIBRATION", message, {"type": event_type, "summary": message, **safe_payload})
