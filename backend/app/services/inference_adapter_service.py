import time

from app.schemas.log import LogLevel
from app.schemas.model_registry import (
    InferenceResult,
    ModelTestInferenceRequest,
    OpenCVCircleTestRequest,
)
from app.services.log_service import JsonlLogService
from app.services.model_registry_service import ModelRegistryService
from app.services.opencv_stub_detector import OpenCVCircleDetector


class InferenceAdapterService:
    def __init__(self, registry: ModelRegistryService, logger: JsonlLogService) -> None:
        self.registry = registry
        self.logger = logger
        self.opencv_stub = OpenCVCircleDetector()
        self.last_event: tuple[str, dict] | None = None

    def test_inference(self, request: ModelTestInferenceRequest) -> InferenceResult:
        self._event("model.test_started", {"model_id": request.model_id, "source": request.source}, "Model test started")
        if request.use_test_adapter or request.model_id in {None, self.opencv_stub.adapter_id}:
            result = self.opencv_stub.run(OpenCVCircleTestRequest(source=request.source, frame_id=request.frame_id))
        else:
            model = self.registry.get_model(request.model_id)
            result = InferenceResult(
                frame_id=request.frame_id,
                source=request.source,
                model_id=model.model_id,
                adapter="mock",
                detections=[],
                latency_ms=0.0,
                warnings=["model uploaded but adapter not available"],
                errors=[],
                no_physical_command_generated=True,
            )
        result = result.model_copy(update={"no_physical_command_generated": True})
        self._event("model.test_completed", result.model_dump(mode="json"), "Model test completed")
        model_id = result.model_id
        if model_id:
            self.registry.record_test_result(model_id, result.model_dump(mode="json"))
        return result

    def opencv_circle_test(self, request: OpenCVCircleTestRequest) -> InferenceResult:
        result = self.opencv_stub.run(request)
        self._event("model.test_completed", result.model_dump(mode="json"), "OpenCV circle test completed")
        return result

    def _event(self, event_type: str, payload: dict, message: str) -> None:
        payload = {"ts": time.time(), **payload}
        self.last_event = (event_type, payload)
        self.logger.emit(LogLevel.INFO, "MODEL", message, payload)
