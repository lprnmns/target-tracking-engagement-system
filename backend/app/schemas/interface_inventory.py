import time
from typing import Literal

from pydantic import BaseModel, Field


InterfaceCategory = Literal[
    "user_interface",
    "rest_api",
    "websocket",
    "mjpeg_stream",
    "camera_interface",
    "vision_model_interface",
    "pico_serial_telemetry",
    "serial_protocol",
    "safety_interface",
    "config_interface",
    "logging_interface",
    "dataset_replay_interface",
    "report_export_interface",
    "deployment_interface",
    "electronic_power_signal_interface_placeholder",
]


class InterfaceRecord(BaseModel):
    interface_id: str
    name: str
    display_name: str | None = None
    category: InterfaceCategory
    category_label: str | None = None
    direction: str
    producer: str
    consumer: str
    transport: str
    protocol: str
    message_format: str
    endpoint_or_port: str
    update_rate: str
    data_fields: list[str]
    safety_boundary: str
    failure_behavior: str
    verification_method: str
    ktr_description: str
    verification_status: str = "implemented"
    readiness_profile_dependency: list[str] = Field(default_factory=list)
    operator_visible: bool = True
    export_evidence_path: str | None = None


class InterfaceInventoryResponse(BaseModel):
    generated_at: float = Field(default_factory=time.time)
    interfaces: list[InterfaceRecord]
    categories: dict[str, int]
    no_physical_command_generated: bool = True


class InterfaceKtrSection(BaseModel):
    generated_at: float = Field(default_factory=time.time)
    markdown: str
    plain_text: str
    no_physical_command_generated: bool = True


class InterfaceExportRecord(BaseModel):
    export_id: str
    created_at: float = Field(default_factory=time.time)
    output_dir: str
    files: list[str]
    no_physical_command_generated: bool = True
