import time

from app.schemas.model_registry import InferenceDetection, InferenceResult, OpenCVCircleTestRequest


class OpenCVCircleDetector:
    """Test-only adapter that produces deterministic balloon-like detections."""

    adapter_id = "opencv-circle-test-adapter"

    def run(self, request: OpenCVCircleTestRequest) -> InferenceResult:
        started = time.perf_counter()
        width = request.width
        height = request.height
        cx = width * 0.58
        cy = height * 0.42
        radius = min(width, height) * 0.08
        x1 = max(0.0, cx - radius)
        y1 = max(0.0, cy - radius)
        x2 = min(float(width), cx + radius)
        y2 = min(float(height), cy + radius)
        xywh = [x1, y1, x2 - x1, y2 - y1]
        yolo = [(x1 + xywh[2] / 2) / width, (y1 + xywh[3] / 2) / height, xywh[2] / width, xywh[3] / height]
        detection = InferenceDetection(
            detection_id="opencv-circle-1",
            class_id=0,
            class_name="balloon",
            confidence=0.82,
            bbox_xyxy_pixel=[round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)],
            bbox_xywh_pixel=[round(value, 2) for value in xywh],
            bbox_yolo_normalized=[round(value, 6) for value in yolo],
            source="opencv_stub",
            is_balloon=True,
        )
        latency_ms = round((time.perf_counter() - started) * 1000 + 4.5, 3)
        return InferenceResult(
            frame_id=request.frame_id,
            source=request.source,
            model_id=self.adapter_id,
            adapter="opencv_stub",
            detections=[detection],
            latency_ms=latency_ms,
            preprocess_ms=1.0,
            inference_ms=max(0.1, latency_ms - 1.8),
            postprocess_ms=0.8,
            warnings=["OpenCV daire algılayıcı yalnızca test adaptörüdür; production model değildir."],
            no_physical_command_generated=True,
        )
