from collections import deque


class MockSerialTransport:
    def __init__(self) -> None:
        self.tx: deque[bytes] = deque()
        self.rx: deque[bytes] = deque()
        self.opened = True

    def write(self, data: bytes) -> int:
        if not self.opened:
            raise RuntimeError("mock serial transport is closed")
        self.tx.append(data)
        return len(data)

    def inject_rx(self, data: bytes) -> None:
        self.rx.append(data)

    def read_line(self) -> bytes | None:
        if not self.rx:
            return None
        return self.rx.popleft()

    def close(self) -> None:
        self.opened = False
