import math
import time

from app.schemas.vision import AimPoint, BalloonDetection, BBox, BodyDetection, TrackPlaceholder, VisionEvent


class MockVisionGenerator:
    def __init__(self) -> None:
        self.frame_id = 0

    def next_event(self, source: str, width: int, height: int) -> VisionEvent:
        self.frame_id += 1
        now_ms = int(time.time() * 1000)
        phase = self.frame_id / 12
        body_x = int((width * 0.18) + (math.sin(phase) + 1) * width * 0.18)
        body_y = int(height * 0.28)
        body_w = int(width * 0.22)
        body_h = int(height * 0.24)
        balloon_x = body_x + int(body_w * 0.58)
        balloon_y = body_y + body_h + 22
        warnings = []
        if self.frame_id % 17 == 0:
            warnings.append("mock_low_contrast_frame")

        body = BodyDetection(
            id=1,
            class_name="helicopter",
            class_id=1,
            confidence=0.86,
            bbox=BBox(x=body_x, y=body_y, w=body_w, h=body_h),
            source="mock",
            color_hint="enemy_candidate",
            stable_frames=min(5, self.frame_id),
            target_team="enemy",
            range_m=8.7,
        )
        balloon = BalloonDetection(
            id=1,
            confidence=0.91,
            bbox=BBox(x=balloon_x - 16, y=balloon_y - 16, w=32, h=32),
            center_x=balloon_x,
            center_y=balloon_y,
            source="mock",
        )
        return VisionEvent(
            frame_id=self.frame_id,
            timestamp_ms=now_ms,
            source=source,
            fps=15.0,
            preprocess_ms=2.1,
            inference_ms=8.4,
            postprocess_ms=1.6,
            total_latency_ms=12.1,
            body_detections=[body],
            balloon_detections=[balloon],
            tracks=[TrackPlaceholder(track_id=1, detection_id=1, stable_frames=body.stable_frames)],
            aim_points=[AimPoint(id=1, x=balloon.center_x, y=balloon.center_y, source="mock")],
            warnings=warnings,
        )
