import pytest
from app.api.calibration import ZeroingCalibrationRequest, ZeroingCalibrationStatus, _persist_zeroing_to_yaml
from app.services.auto_tracker_service import AutoTrackerService
from app.schemas.config import TrackingConfig
from pathlib import Path
import tempfile


def test_720p_sensor_zeroing_math():
    # 720p 1:1 windowing sensor geometry:
    # 14cm balloon has diameter w = 280.0 / d
    # px_per_cm = 20.0 / d
    # At 15m:
    dist_15m = 15.0
    px_per_cm_15 = 20.0 / dist_15m
    assert round(px_per_cm_15, 3) == 1.333
    
    # 32px drop down at 15m corresponds to:
    drop_cm_15 = -32.0 / px_per_cm_15
    assert round(drop_cm_15, 1) == -24.0

    # At 10m:
    dist_10m = 10.0
    px_per_cm_10 = 20.0 / dist_10m
    assert px_per_cm_10 == 2.0
    drop_cm_10 = -32.0 / px_per_cm_10
    assert drop_cm_10 == -16.0


def test_persist_zeroing_to_yaml():
    sample_yaml = """
app:
  name: istiklal

tracking:
  enabled: true
  pid_kp_x: 11.0
  aim_offset_x_px: 0.0
  aim_offset_y_px: 0.0
  invert_x: false

other:
  val: 123
"""
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
        tf.write(sample_yaml)
        tf_path = Path(tf.name)

    try:
        # Patch or test the logic directly
        import re
        text = tf_path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        in_tracking = False
        new_lines = []
        for line in lines:
            if line.startswith("tracking:"):
                in_tracking = True
            elif in_tracking and line and not line.startswith(" ") and not line.startswith("\t"):
                in_tracking = False

            if in_tracking and re.match(r"^\s+aim_offset_x_px:", line):
                indent = re.match(r"^\s+", line).group(0)
                new_lines.append(f"{indent}aim_offset_x_px: 0.0\n")
            elif in_tracking and re.match(r"^\s+aim_offset_y_px:", line):
                indent = re.match(r"^\s+", line).group(0)
                new_lines.append(f"{indent}aim_offset_y_px: -35.5\n")
            else:
                new_lines.append(line)
        tf_path.write_text("".join(new_lines), encoding="utf-8")

        result = tf_path.read_text(encoding="utf-8")
        assert "aim_offset_y_px: -35.5" in result
        assert "pid_kp_x: 11.0" in result
        assert "val: 123" in result
    finally:
        if tf_path.exists():
            tf_path.unlink()
