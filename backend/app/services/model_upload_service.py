from app.schemas.model_registry import ModelMetadata, ModelUploadRequest
from app.services.model_registry_service import ModelRegistryService


class ModelUploadService:
    def __init__(self, registry: ModelRegistryService) -> None:
        self.registry = registry

    def upload(self, request: ModelUploadRequest) -> ModelMetadata:
        return self.registry.upload(request)
