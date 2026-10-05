from collections import deque


class MockSerialTransport:
    def __init__(self, lines: list[bytes] | None = None) -> None:
        self._lines = deque(lines or [])
        self._is_open = False
        self.writes: list[bytes] = []

    def open(self) -> None:
        self._is_open = True

    def close(self) -> None:
        self._is_open = False

    def readline(self) -> bytes:
        if not self._is_open or not self._lines:
            return b""
        return self._lines.popleft()

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    @property
    def is_open(self) -> bool:
        return self._is_open
