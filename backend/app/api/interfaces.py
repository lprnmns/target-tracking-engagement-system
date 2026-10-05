from fastapi import APIRouter, Depends

from app.api.deps import get_runtime
from app.schemas.interface_inventory import InterfaceExportRecord, InterfaceInventoryResponse, InterfaceKtrSection
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/interfaces", tags=["interfaces"])


@router.get("", response_model=InterfaceInventoryResponse)
def root_inventory(runtime: RuntimeState = Depends(get_runtime)) -> InterfaceInventoryResponse:
    return runtime.interface_inventory.inventory()


@router.get("/inventory", response_model=InterfaceInventoryResponse)
def inventory(runtime: RuntimeState = Depends(get_runtime)) -> InterfaceInventoryResponse:
    return runtime.interface_inventory.inventory()


@router.get("/ktr-section", response_model=InterfaceKtrSection)
def ktr_section(runtime: RuntimeState = Depends(get_runtime)) -> InterfaceKtrSection:
    return runtime.interface_inventory.ktr_section()


@router.post("/export", response_model=InterfaceExportRecord)
def export(runtime: RuntimeState = Depends(get_runtime)) -> InterfaceExportRecord:
    return runtime.interface_inventory.export()
