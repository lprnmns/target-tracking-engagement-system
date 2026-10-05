class PySerialTransport:
    def __init__(self, port: str, baudrate: int, timeout: float = 0.05) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial = None

    def open(self) -> None:
        try:
            import serial  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on host environment
            raise RuntimeError("pyserial is not installed") from exc
        self._serial = serial.Serial(self.port, self.baudrate, timeout=self.timeout, write_timeout=self.timeout)

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
        self._serial = None

    def readline(self) -> bytes:
        if self._serial is None:
            return b""
        return bytes(self._serial.readline())

    def write(self, data: bytes) -> int:
        raise RuntimeError("Phase 12 read-only transport forbids writes")

    @property
    def is_open(self) -> bool:
        return bool(self._serial is not None and self._serial.is_open)
