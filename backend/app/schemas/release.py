import time

from pydantic import BaseModel, Field


class ReleaseCheckItem(BaseModel):
    name: str
    status: str
    message: str
    blocking: bool = False
    detail: dict = Field(default_factory=dict)


class ReleaseStatus(BaseModel):
    launcher_available: bool
    frontend_static_available: bool
    writable_runtime_dirs: bool
    offline_readiness: str
    field_profile_saved: bool
    status: str = "warning"
    platform: str | None = None
    python_version: str | None = None
    app_root: str | None = None
    writable_logs: bool = False
    writable_exports: bool = False
    config_loaded: bool = False
    model_dir_present: bool = False
    active_model_loaded: bool = False
    camera_devices_detected: int = 0
    serial_devices_detected: int = 0
    pico_candidate_count: int = 0
    hardware_command_enabled: bool = False
    dry_run: bool = True
    no_fire: bool = True
    safety_invariant_ok: bool = True
    release_manifest_path: str | None = None
    cold_start_evidence: dict = Field(default_factory=dict)
    suggested_actions: list[str] = Field(default_factory=list)
    checks: list[ReleaseCheckItem] = Field(default_factory=list)
    generated_at: float = Field(default_factory=time.time)
    no_physical_command_generated: bool = True


class ReleaseManifest(BaseModel):
    commit_hash: str
    phase: str = "Phase 22"
    build_id: str
    generated_at: float = Field(default_factory=time.time)
    platform: str
    included_components: list[str]
    excluded_runtime_dirs: list[str]
    safety_invariant: dict
    launcher_files: list[str]
    frontend_dist_present: bool
    backend_entrypoint: str
    dependency_strategy: str
    no_physical_command_generated: bool = True


class ReleasePackageRecord(BaseModel):
    package_id: str
    output_dir: str
    zip_path: str
    files_count: int
    checksums_path: str
    manifest_path: str
    commit_hash: str | None = None
    source_commit: str
    package_generated_commit: str
    package_workflow_commit: str
    report_commit: str
    checksum_status: str = "passed"
    release_demo_ready: bool = False
    competition_ready: bool = False
    dataset_ready_for_training: bool = False
    no_physical_command_generated: bool = True
    safety_invariant: str = "DISARMED + NO_FIRE + dry_run=true + hardware_enabled=false + physical_command_enabled=false"
    created_at: float = Field(default_factory=time.time)


class CleanroomSmokeEndpoint(BaseModel):
    method: str = "GET"
    path: str
    status_code: int
    ok: bool


class CleanroomVerificationRecord(BaseModel):
    run_id: str
    package_id: str
    zip_path: str
    extract_path: str
    launch_command: str
    smoke_status: str
    endpoints: list[CleanroomSmokeEndpoint] = Field(default_factory=list)
    endpoints_passed: int = 0
    endpoints_total: int = 0
    frontend_dist_present: bool = False
    backend_present: bool = False
    forbidden_entries: list[str] = Field(default_factory=list)
    secrets_or_tokens: list[str] = Field(default_factory=list)
    launcher_hardcoded_repo_path: bool = False
    release_demo_ready: bool = True
    competition_ready: bool = False
    no_physical_command_generated: bool = True
    report_paths: dict[str, str] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
