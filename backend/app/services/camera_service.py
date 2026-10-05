import asyncio
from collections.abc import AsyncIterator

from app.mocks.mock_camera import MockCamera
from app.schemas.config import AppConfig
from app.schemas.vision import CameraSelectRequest, CameraSource, CameraStatus


class CameraService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.camera_mode = config.camera.camera_mode
        self.camera_source = config.camera.camera_source
        self.mock = MockCamera(
            width=config.camera.stream_width,
            height=config.camera.stream_height,
            fps=config.camera.stream_fps,
        )
        self.last_error: str | None = None

    def status(self) -> CameraStatus:
        return CameraStatus(
            camera_mode=self.camera_mode,
            source=self.camera_source,
            connected=self.camera_mode == "mock" or self.mock.running,
            running=self.mock.running,
            stream_enabled=self.config.camera.stream_enabled,
            width=self.config.camera.stream_width,
            height=self.config.camera.stream_height,
            fps=self.config.camera.stream_fps,
            last_error=self.last_error,
        )

    def sources(self) -> list[CameraSource]:
        return [
            CameraSource(id="mock", label="Mock camera placeholder", mode="mock", available=True),
            CameraSource(id="webcam:0", label="Optional webcam index 0", mode="webcam", available=False),
        ]

    def select(self, request: CameraSelectRequest) -> CameraStatus:
        if request.camera_mode not in {"mock", "image", "webcam"}:
            self.last_error = "invalid_camera_mode"
            return self.status()
        self.camera_mode = request.camera_mode
        self.camera_source = request.camera_source
        if self.camera_mode != "mock":
            self.last_error = "real_camera_optional_not_started"
        else:
            self.last_error = None
        return self.status()

    def start(self) -> None:
        self.mock.start()

    def stop(self) -> None:
        self.mock.stop()

    def snapshot(self) -> bytes:
        if not self.mock.running:
            self.mock.start()
        return self.mock.jpeg_frame()

    async def mjpeg_stream(self) -> AsyncIterator[bytes]:
        if not self.mock.running:
            self.mock.start()
        delay = 1 / max(self.config.camera.stream_fps, 1)
        while True:
            frame = self.mock.jpeg_frame()
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            await asyncio.sleep(delay)
