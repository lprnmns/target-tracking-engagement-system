"""Metric Aşama 3 range evidence tied to a concrete body-model hash.

This intentionally does not use the legacy identity-homography placeholder.
It estimates range from observed apparent target height only after the team has
recorded 5/10/15 m field observations for every competition class.  Model
changes invalidate the profile automatically.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import time
import uuid
from pathlib import Path

from app.schemas.stage3_range import (
    STAGE3_CLASSES,
    Stage3RangeCalibrationStatus,
    Stage3RangeClassFit,
    Stage3RangeEstimate,
    Stage3RangeObservation,
    Stage3RangeObservationCreate,
)
from app.schemas.vision import BodyDetection


REQUIRED_DISTANCES_M = (5.0, 10.0, 15.0)
DISTANCE_TOLERANCE_M = 0.35
MAX_ACCEPTABLE_MEAN_ERROR_M = 0.75


class Stage3RangeCalibrationService:
    def __init__(self, logger, path: Path) -> None:
        self.logger = logger
        self.path = path
        self._status = Stage3RangeCalibrationStatus()
        self._load()

    def status(self, body_model_id: str | None = None, body_model_path: str | None = None) -> Stage3RangeCalibrationStatus:
        if self._status.valid and not self._model_matches(body_model_id, body_model_path):
            return self._status.model_copy(
                update={
                    "valid": False,
                    "reason_codes": ["A3_RANGE_MODEL_FINGERPRINT_MISMATCH"],
                    "updated_at": time.time(),
                }
            )
        return self._status

    def add_observation(self, request: Stage3RangeObservationCreate) -> Stage3RangeCalibrationStatus:
        observation = Stage3RangeObservation(observation_id=f"rng-{uuid.uuid4().hex[:10]}", **request.model_dump())
        observations = [*self._status.observations, observation]
        self._status = self._status.model_copy(
            update={
                "valid": False,
                "reason_codes": ["A3_RANGE_REVALIDATION_REQUIRED"],
                "observations": observations,
                "fits": [],
                "calibration_hash": None,
                "validated_at": None,
                "updated_at": time.time(),
            }
        )
        self._persist()
        return self._status

    def remove_observation(self, observation_id: str) -> Stage3RangeCalibrationStatus:
        observations = [item for item in self._status.observations if item.observation_id != observation_id]
        if len(observations) == len(self._status.observations):
            raise KeyError(observation_id)
        self._status = self._status.model_copy(
            update={
                "valid": False,
                "reason_codes": ["A3_RANGE_REVALIDATION_REQUIRED"],
                "observations": observations,
                "fits": [],
                "calibration_hash": None,
                "validated_at": None,
                "updated_at": time.time(),
            }
        )
        self._persist()
        return self._status

    def validate(self, body_model_id: str | None, body_model_path: str | None) -> Stage3RangeCalibrationStatus:
        if not body_model_id or not body_model_path or not Path(body_model_path).is_file():
            return self._set_invalid(["A3_RANGE_BODY_MODEL_UNAVAILABLE"])
        missing: list[str] = []
        fits: list[Stage3RangeClassFit] = []
        for class_name in STAGE3_CLASSES:
            samples = [item for item in self._status.observations if item.class_name == class_name]
            distances = sorted({item.distance_m for item in samples})
            if not all(any(abs(distance - required) <= DISTANCE_TOLERANCE_M for distance in distances) for required in REQUIRED_DISTANCES_M):
                missing.append(f"A3_RANGE_REQUIRED_DISTANCES_MISSING:{class_name}")
                continue
            scales = [item.distance_m * item.bbox_height_px for item in samples]
            scale = statistics.median(scales)
            errors = [abs(scale / item.bbox_height_px - item.distance_m) for item in samples]
            mean_error = statistics.mean(errors)
            if mean_error > MAX_ACCEPTABLE_MEAN_ERROR_M:
                missing.append(f"A3_RANGE_CALIBRATION_ERROR_TOO_HIGH:{class_name}")
                continue
            # Never advertise zero uncertainty from a small fitted sample.
            uncertainty = max(0.25, round(mean_error * 1.25, 4))
            fits.append(
                Stage3RangeClassFit(
                    class_name=class_name,
                    scale_px_m=round(scale, 5),
                    sample_count=len(samples),
                    calibration_distances_m=distances,
                    mean_abs_error_m=round(mean_error, 5),
                    uncertainty_m=uncertainty,
                )
            )
        if missing:
            return self._set_invalid(sorted(missing))
        model_hash = self._file_hash(Path(body_model_path))
        payload = {
            "body_model_id": body_model_id,
            "body_model_hash": model_hash,
            "observations": [item.model_dump(mode="json") for item in self._status.observations],
            "fits": [item.model_dump(mode="json") for item in fits],
        }
        calibration_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        self._status = Stage3RangeCalibrationStatus(
            valid=True,
            reason_codes=[],
            body_model_id=body_model_id,
            body_model_hash=model_hash,
            calibration_hash=calibration_hash,
            observations=self._status.observations,
            fits=fits,
            validated_at=time.time(),
            updated_at=time.time(),
        )
        self._persist()
        return self._status

    def reset(self) -> Stage3RangeCalibrationStatus:
        self._status = Stage3RangeCalibrationStatus()
        self._persist()
        return self._status

    def estimate(self, body: BodyDetection, body_model_id: str | None, body_model_path: str | None) -> Stage3RangeEstimate:
        status = self.status(body_model_id, body_model_path)
        if not status.valid:
            return Stage3RangeEstimate(class_name=body.class_name, reason_code=status.reason_codes[0] if status.reason_codes else "A3_RANGE_CALIBRATION_UNAVAILABLE")
        fit = next((item for item in status.fits if item.class_name == body.class_name), None)
        if fit is None:
            return Stage3RangeEstimate(class_name=body.class_name, calibration_hash=status.calibration_hash, reason_code="A3_RANGE_CLASS_CALIBRATION_MISSING")
        if body.bbox.h <= 0:
            return Stage3RangeEstimate(class_name=body.class_name, calibration_hash=status.calibration_hash, reason_code="A3_RANGE_INVALID_BODY_BBOX")
        range_m = fit.scale_px_m / body.bbox.h
        uncertainty = fit.uncertainty_m
        return Stage3RangeEstimate(
            class_name=body.class_name,
            range_m=round(range_m, 4),
            uncertainty_m=uncertainty,
            lower_bound_m=round(max(0.0, range_m - uncertainty), 4),
            upper_bound_m=round(range_m + uncertainty, 4),
            calibration_hash=status.calibration_hash,
            ready=True,
            reason_code="A3_RANGE_READY",
        )

    def attach_estimates(self, bodies: list[BodyDetection], body_model_id: str | None, body_model_path: str | None) -> list[BodyDetection]:
        enriched: list[BodyDetection] = []
        for body in bodies:
            estimate = self.estimate(body, body_model_id, body_model_path)
            enriched.append(
                body.model_copy(
                    update={
                        "range_m": estimate.range_m,
                        "range_uncertainty_m": estimate.uncertainty_m,
                        "range_calibration_hash": estimate.calibration_hash,
                    }
                )
            )
        return enriched

    def ready_for(self, body: BodyDetection | None, body_model_id: str | None, body_model_path: str | None) -> tuple[bool, str]:
        if body is None:
            return False, "No body is available for metric range evaluation."
        estimate = self.estimate(body, body_model_id, body_model_path)
        if not estimate.ready:
            return False, estimate.reason_code
        return True, "Verified metric range profile and uncertainty are available."

    def _set_invalid(self, reason_codes: list[str]) -> Stage3RangeCalibrationStatus:
        self._status = self._status.model_copy(
            update={
                "valid": False,
                "reason_codes": reason_codes,
                "fits": [],
                "calibration_hash": None,
                "validated_at": None,
                "updated_at": time.time(),
            }
        )
        self._persist()
        return self._status

    def _model_matches(self, body_model_id: str | None, body_model_path: str | None) -> bool:
        return bool(
            body_model_id
            and body_model_path
            and Path(body_model_path).is_file()
            and body_model_id == self._status.body_model_id
            and self._file_hash(Path(body_model_path)) == self._status.body_model_hash
        )

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _load(self) -> None:
        try:
            content = self.path.read_text(encoding="utf-8")
            self._status = Stage3RangeCalibrationStatus.model_validate_json(content)
        except (OSError, ValueError):
            self._status = Stage3RangeCalibrationStatus()

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self._status.model_dump_json(indent=2), encoding="utf-8")
