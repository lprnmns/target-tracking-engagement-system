import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError


class SerialJsonError(ValueError):
    pass


class PcMessageType(StrEnum):
    HEARTBEAT = "heartbeat"
    DISARM = "disarm"
    SELF_TEST = "self_test"
    SET_MODE = "set_mode"
    FIRE_REQUEST = "fire_request"
    JOG_MOTOR = "jog_motor"
    SET_SERVO_POSITION = "set_servo_position"


class PicoMessageType(StrEnum):
    ACK = "ack"
    NACK = "nack"
    TELEMETRY = "telemetry"
    ERROR = "error"
    HEARTBEAT = "heartbeat"


class SerialJsonMessage(BaseModel):
    type: str
    seq: int = Field(ge=0)


class HeartbeatTx(SerialJsonMessage):
    type: Literal["heartbeat"] = "heartbeat"
    timestamp_ms: int = Field(ge=0)


class DisarmTx(SerialJsonMessage):
    type: Literal["disarm"] = "disarm"
    reason: str


class SelfTestTx(SerialJsonMessage):
    type: Literal["self_test"] = "self_test"
    test: str


class SetModeTx(SerialJsonMessage):
    type: Literal["set_mode"] = "set_mode"
    mode: str


class RiskyCommandTx(SerialJsonMessage):
    type: Literal["fire_request", "jog_motor", "set_servo_position"]
    reason: str | None = None


class AckRx(SerialJsonMessage):
    type: Literal["ack"] = "ack"
    accepted: bool = True


class NackRx(SerialJsonMessage):
    type: Literal["nack"] = "nack"
    reason: str


class TelemetryRx(SerialJsonMessage):
    type: Literal["telemetry"] = "telemetry"
    estop_state: bool
    driver_enabled: bool
    pan_position_steps: int
    tilt_position_steps: int
    last_error: str | None = None


class ErrorRx(SerialJsonMessage):
    type: Literal["error"] = "error"
    code: str
    message: str


class HeartbeatRx(SerialJsonMessage):
    type: Literal["heartbeat"] = "heartbeat"
    timestamp_ms: int = Field(ge=0)


TxMessage = HeartbeatTx | DisarmTx | SelfTestTx | SetModeTx | RiskyCommandTx
RxMessage = AckRx | NackRx | TelemetryRx | ErrorRx | HeartbeatRx

TX_ADAPTER = TypeAdapter(TxMessage)
RX_ADAPTER = TypeAdapter(RxMessage)


def encode_json_line(message: TxMessage | RxMessage | dict[str, Any]) -> bytes:
    if isinstance(message, BaseModel):
        payload = message.model_dump(mode="json")
    else:
        payload = message
    return (json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def decode_tx_json_line(line: bytes | str) -> TxMessage:
    return _decode(line, TX_ADAPTER)


def decode_rx_json_line(line: bytes | str) -> RxMessage:
    return _decode(line, RX_ADAPTER)


def _decode(line: bytes | str, adapter: TypeAdapter[TxMessage] | TypeAdapter[RxMessage]) -> Any:
    try:
        text = line.decode("utf-8") if isinstance(line, bytes) else line
        raw = json.loads(text.strip())
        return adapter.validate_python(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise SerialJsonError(str(exc)) from exc

