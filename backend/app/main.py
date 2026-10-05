import asyncio
from contextlib import suppress
import hashlib
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.calibration import router as calibration_router
from app.api.color import router as color_router
from app.api.hardware import router as hardware_router
from app.api.devices import router as devices_router
from app.api.device_profiles import router as device_profiles_router
from app.api.first_run import router as first_run_router
from app.api.interfaces import router as interfaces_router
from app.api.logs import router as logs_router
from app.api.routes_health import router as health_router
from app.api.decision import router as decision_router
from app.api.data_lab import router as data_lab_router
from app.api.demo import router as demo_router
from app.api.digital_twin import router as digital_twin_router
from app.api.engagement_evidence import router as engagement_evidence_router
from app.api.dataset import annotation_router, dataset_router, session_router
from app.api.models import router as models_router
from app.api.mission import router as mission_router
from app.api.motion import router as motion_router
from app.api.operator_config import router as operator_config_router
from app.api.operation import router as operation_router
from app.api.person_safety import router as person_safety_router
from app.api.replay import router as replay_router
from app.api.reports import router as reports_router
from app.api.release import router as release_router
from app.api.self_test import router as self_test_router
from app.api.setup import router as setup_router
from app.api.stage3 import router as stage3_router
from app.api.routes_motor import router as motor_router
from app.api.routes_pico import router as pico_router
from app.api.ballistics import router as ballistics_router
from app.api.routes_safety import router as safety_router
from app.api.safety_zones import router as safety_zones_router
from app.api.routes_serial import router as serial_router
from app.api.routes_system import router as system_router
from app.api.routes_ws import router as websocket_router
from app.api.vision import camera_router, vision_router
from app.services.config_service import ConfigService, default_config_path
from app.services.log_service import default_log_dir
from app.services.runtime_state import build_runtime
from app.services.storage_paths import project_root


def create_app(
    config_path: Path | None = None,
    log_dir: Path | None = None,
    report_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="ISTIKLAL Command Center Backend", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:8014",
            "http://localhost:8014",
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    config = ConfigService(config_path or default_config_path()).load()
    app.state.runtime = build_runtime(config=config, log_dir=log_dir or default_log_dir(), report_dir=report_dir)

    @app.on_event("startup")
    async def start_gateway_maintenance() -> None:
        async def maintain() -> None:
            while True:
                await asyncio.to_thread(app.state.runtime.command_gateway.maintenance_tick, app.state.runtime)
                await asyncio.sleep(0.1)

        app.state.gateway_maintenance_task = asyncio.create_task(maintain())
        try:
            app.state.runtime.camera.start()
            app.state.runtime.vision_pipeline.start()
            app.state.warmup_task = asyncio.create_task(
                asyncio.to_thread(app.state.runtime.vision_pipeline.preflight_warmup)
            )
        except Exception:
            pass

    @app.on_event("shutdown")
    async def stop_gateway_maintenance() -> None:
        task = getattr(app.state, "gateway_maintenance_task", None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    app.include_router(health_router)
    app.include_router(decision_router)
    app.include_router(system_router)
    app.include_router(safety_router)
    app.include_router(safety_zones_router)
    app.include_router(motor_router)
    app.include_router(hardware_router)
    app.include_router(devices_router)
    app.include_router(device_profiles_router)
    app.include_router(motion_router)
    app.include_router(operator_config_router)
    app.include_router(operation_router)
    app.include_router(setup_router)
    app.include_router(pico_router)
    app.include_router(serial_router)
    app.include_router(calibration_router)
    app.include_router(ballistics_router)
    app.include_router(color_router)
    app.include_router(vision_router)
    app.include_router(camera_router)
    app.include_router(models_router)
    app.include_router(mission_router)
    app.include_router(stage3_router)
    app.include_router(person_safety_router)
    app.include_router(session_router)
    app.include_router(annotation_router)
    app.include_router(dataset_router)
    app.include_router(data_lab_router)
    app.include_router(demo_router)
    app.include_router(digital_twin_router)
    app.include_router(engagement_evidence_router)
    app.include_router(replay_router)
    app.include_router(self_test_router)
    app.include_router(first_run_router)
    app.include_router(interfaces_router)
    app.include_router(reports_router)
    app.include_router(release_router)
    app.include_router(logs_router)
    app.include_router(websocket_router)
    _enable_static_frontend(app, config)
    return app


def _enable_static_frontend(app: FastAPI, config) -> None:
    frontend_dist = project_root() / "frontend" / "dist"
    index_path = frontend_dist / "index.html"
    if not config.runtime_mode.frontend_static_enabled or not index_path.exists():
        return

    # Dedicated high-throughput in-memory cache for large immutable 3D/WASM assets
    # (e.g. .glb and .wasm). Starlette StaticFiles / FileResponse streams files in 64 KB
    # anyio chunks, which suffer extreme starvation (down to 40 KB/s) when background
    # computer vision and tracking tasks monopolize the Python GIL. Serving in-memory
    # bytes in a single ASGI message restores localhost transfer to >500 MB/s, while
    # Cache-Control: public, max-age=31536000, immutable guarantees zero-byte / zero-ms
    # loads on subsequent F5 reloads.
    # Note: JSON calibration files and general assets remain strictly non-immutable.
    _BINARY_ASSET_CACHE: dict[Path, tuple[bytes, str]] = {}
    _IMMUTABLE_MEDIA_TYPES = {
        ".glb": "model/gltf-binary",
        ".wasm": "application/wasm",
    }

    # Pre-cache critical 3D binary assets into memory at startup to guarantee 0 ms disk wait
    for pre_asset in [
        frontend_dist / "assets" / "digital-twin" / "ktr1_kinematic_world_phase55_draco.glb",
        frontend_dist / "assets" / "digital-twin" / "ktr1_kinematic_world_phase55.glb",
        frontend_dist / "draco" / "draco_decoder.wasm",
    ]:
        if pre_asset.is_file():
            try:
                _data = pre_asset.read_bytes()
                _BINARY_ASSET_CACHE[pre_asset] = (_data, f'"{hashlib.md5(_data).hexdigest()}"')
            except Exception:
                pass

    def _serve_immutable_binary(path: Path, request: Request, method: str = "GET") -> Response:
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        ext = path.suffix.lower()
        media_type = _IMMUTABLE_MEDIA_TYPES.get(ext, "application/octet-stream")

        cached = _BINARY_ASSET_CACHE.get(path)
        if cached is None:
            data = path.read_bytes()
            etag = f'"{hashlib.md5(data).hexdigest()}"'
            _BINARY_ASSET_CACHE[path] = (data, etag)
        else:
            data, etag = cached

        if_none_match = request.headers.get("if-none-match")
        headers = {
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": etag,
            "Accept-Ranges": "bytes",
            "Content-Length": str(len(data)),
        }
        if if_none_match and if_none_match.strip() == etag:
            return Response(status_code=304, headers=headers)

        if method == "HEAD":
            head_headers = {k: v for k, v in headers.items() if k.lower() != "content-length"}
            return Response(status_code=200, media_type=media_type, headers=head_headers)

        return Response(content=data, media_type=media_type, headers=headers)

    @app.get("/assets/digital-twin/{asset_name:path}", include_in_schema=False)
    @app.head("/assets/digital-twin/{asset_name:path}", include_in_schema=False)
    def serve_digital_twin_asset(asset_name: str, request: Request) -> Response:
        target = frontend_dist / "assets" / "digital-twin" / asset_name
        if target.suffix.lower() in _IMMUTABLE_MEDIA_TYPES:
            return _serve_immutable_binary(target, request, request.method)
        # JSON and other metadata must NOT have immutable cache headers
        if target.is_file():
            return FileResponse(target)
        raise HTTPException(status_code=404, detail="Not found")

    @app.get("/draco/{asset_name:path}", include_in_schema=False)
    @app.head("/draco/{asset_name:path}", include_in_schema=False)
    def serve_draco_asset(asset_name: str, request: Request) -> Response:
        target = frontend_dist / "draco" / asset_name
        if target.suffix.lower() in _IMMUTABLE_MEDIA_TYPES:
            return _serve_immutable_binary(target, request, request.method)
        if target.is_file():
            return FileResponse(target)
        raise HTTPException(status_code=404, detail="Not found")

    assets_path = frontend_dist / "assets"
    if assets_path.exists():
        app.mount("/assets", StaticFiles(directory=assets_path), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    @app.head("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str, request: Request) -> Response:
        if full_path.startswith("api/") or full_path == "api" or full_path.startswith("ws"):
            raise HTTPException(status_code=404, detail="Not found")
        requested = frontend_dist / full_path
        if requested.is_file():
            if requested.suffix.lower() in _IMMUTABLE_MEDIA_TYPES:
                return _serve_immutable_binary(requested, request, request.method)
            return FileResponse(requested)
        # index.html contains the hashed Vite entrypoint.  It must not be
        # cached across a one-click deployment, otherwise an operator can
        # keep running the previous cockpit bundle while Setup already shows
        # the new perception contract.
        return FileResponse(
            index_path,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )


app = create_app()
