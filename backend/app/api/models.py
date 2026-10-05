from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_runtime
from app.schemas.model_registry import (
    ActiveModels,
    InferenceResult,
    ModelActivationRequest,
    ModelMetadata,
    ModelMetadataUpdate,
    ModelTestInferenceRequest,
    ModelUploadRequest,
    ModelValidationResult,
    OpenCVCircleTestRequest,
)
from app.schemas.model_package import (
    ModelPackageActivateRequest,
    ModelPackageBenchmarkResult,
    ModelPackageImportRequest,
    ModelPackageRecord,
    ModelPackageTestRequest,
    ModelPackageTestResult,
    ModelPackageValidationResult,
    RecommendedSettingsApplyResult,
)
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("", response_model=list[ModelMetadata])
def list_models(runtime: RuntimeState = Depends(get_runtime)) -> list[ModelMetadata]:
    return runtime.model_registry.list_models()


@router.post("/upload", response_model=ModelMetadata)
def upload_model(request: ModelUploadRequest, runtime: RuntimeState = Depends(get_runtime)) -> ModelMetadata:
    try:
        return runtime.model_upload.upload(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/active")
def active_models(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    active = runtime.model_registry.active_models()
    semantic = runtime.model_packages.active_package_summary()
    return {**active.model_dump(mode="json"), **semantic, "no_physical_command_generated": True}


@router.post("/test-inference", response_model=InferenceResult)
def test_inference(request: ModelTestInferenceRequest, runtime: RuntimeState = Depends(get_runtime)) -> InferenceResult:
    try:
        return runtime.inference_adapter.test_inference(request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="model not found") from exc


@router.post("/opencv-circle-test", response_model=InferenceResult)
def opencv_circle_test(request: OpenCVCircleTestRequest, runtime: RuntimeState = Depends(get_runtime)) -> InferenceResult:
    return runtime.inference_adapter.opencv_circle_test(request)


@router.get("/packages", response_model=list[ModelPackageRecord])
def list_model_packages(runtime: RuntimeState = Depends(get_runtime)) -> list[ModelPackageRecord]:
    return runtime.model_packages.list_packages()


@router.post("/packages/import", response_model=ModelPackageRecord)
def import_model_package(
    request: ModelPackageImportRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> ModelPackageRecord:
    try:
        return runtime.model_packages.import_package(request)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/packages/{model_id}", response_model=ModelPackageRecord)
def get_model_package(model_id: str, runtime: RuntimeState = Depends(get_runtime)) -> ModelPackageRecord:
    try:
        return runtime.model_packages.get_package(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="model package not found") from exc


@router.post("/packages/{model_id}/validate", response_model=ModelPackageValidationResult)
def validate_model_package(model_id: str, runtime: RuntimeState = Depends(get_runtime)) -> ModelPackageValidationResult:
    try:
        return runtime.model_packages.validate_package(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="model package not found") from exc


@router.post("/packages/{model_id}/activate")
def activate_model_package(
    model_id: str,
    request: ModelPackageActivateRequest = ModelPackageActivateRequest(),
    runtime: RuntimeState = Depends(get_runtime),
) -> dict:
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")
    try:
        return runtime.model_packages.activate_package(model_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="model package not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/packages/{model_id}/deactivate")
def deactivate_model_package(model_id: str, runtime: RuntimeState = Depends(get_runtime)) -> dict:
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")
    try:
        return runtime.model_packages.deactivate_package(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="model package not found") from exc


@router.post("/packages/{model_id}/test", response_model=ModelPackageTestResult)
def test_model_package(
    model_id: str,
    request: ModelPackageTestRequest = ModelPackageTestRequest(),
    runtime: RuntimeState = Depends(get_runtime),
) -> ModelPackageTestResult:
    try:
        return runtime.model_packages.test_package(model_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="model package not found") from exc


@router.post("/packages/{model_id}/benchmark", response_model=ModelPackageBenchmarkResult)
def benchmark_model_package(model_id: str, runtime: RuntimeState = Depends(get_runtime)) -> ModelPackageBenchmarkResult:
    try:
        return runtime.model_packages.benchmark_package(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="model package not found") from exc


@router.post("/packages/{model_id}/apply-recommended-settings", response_model=RecommendedSettingsApplyResult)
def apply_model_recommended_settings(
    model_id: str,
    runtime: RuntimeState = Depends(get_runtime),
) -> RecommendedSettingsApplyResult:
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")
    try:
        return runtime.model_packages.apply_recommended_settings(model_id, runtime.vision_runtime)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="model package not found") from exc


@router.get("/{model_id}", response_model=ModelMetadata)
def get_model(model_id: str, runtime: RuntimeState = Depends(get_runtime)) -> ModelMetadata:
    try:
        return runtime.model_registry.get_model(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="model not found") from exc


@router.delete("/{model_id}")
def delete_model(model_id: str, runtime: RuntimeState = Depends(get_runtime)) -> dict:
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")
    try:
        return runtime.model_registry.delete(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="model not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{model_id}/validate", response_model=ModelValidationResult)
def validate_model(model_id: str, runtime: RuntimeState = Depends(get_runtime)) -> ModelValidationResult:
    try:
        return runtime.model_registry.validate_model(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="model not found") from exc


@router.post("/{model_id}/activate", response_model=ActiveModels)
def activate_model(
    model_id: str,
    request: ModelActivationRequest = ModelActivationRequest(),
    runtime: RuntimeState = Depends(get_runtime),
) -> ActiveModels:
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")
    try:
        return runtime.model_registry.activate(model_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="model not found") from exc


@router.post("/{model_id}/deactivate", response_model=ActiveModels)
def deactivate_model(model_id: str, runtime: RuntimeState = Depends(get_runtime)) -> ActiveModels:
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")
    return runtime.model_registry.deactivate(model_id)


@router.put("/{model_id}/metadata", response_model=ModelMetadata)
def update_metadata(
    model_id: str,
    request: ModelMetadataUpdate,
    runtime: RuntimeState = Depends(get_runtime),
) -> ModelMetadata:
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")
    try:
        return runtime.model_registry.update_metadata(model_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="model not found") from exc
