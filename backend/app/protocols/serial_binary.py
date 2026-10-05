from dataclasses import dataclass
from enum import IntEnum

from app.protocols.crc16 import crc16_xmodem

START_BYTE = 0xAA
END_BYTE = 0x55


class BinaryPacketError(ValueError):
    pass


class BinaryMessageType(IntEnum):
    HEARTBEAT = 0x01
    SET_MODE = 0x02
    SET_MOTOR_TARGET = 0x03
    JOG_MOTOR = 0x04
    STOP_MOTION = 0x05
    SET_SERVO_POSITION = 0x06
    FIRE_REQUEST = 0x07
    DISARM = 0x08
    CONFIG_UPDATE = 0x09
    SELF_TEST = 0x0A
    TELEMETRY = 0x81
    ACK = 0x82
    NACK = 0x83
    ERROR = 0x84


@dataclass(frozen=True)
class BinaryPacket:
    message_type: BinaryMessageType
    seq: int
    payload: bytes = b""


def encode_packet(packet: BinaryPacket) -> bytes:
    if not 0 <= packet.seq <= 0xFF:
        raise BinaryPacketError("seq must fit in 1 byte")
    if len(packet.payload) > 0xFF:
        raise BinaryPacketError("payload length must fit in 1 byte")
    header = bytes([packet.message_type, packet.seq, len(packet.payload)]) + packet.payload
    crc = crc16_xmodem(header).to_bytes(2, "big")
    return bytes([START_BYTE]) + header + crc + bytes([END_BYTE])


def decode_packet(data: bytes) -> BinaryPacket:
    if len(data) < 6:
        raise BinaryPacketError("packet too short")
    if data[0] != START_BYTE:
        raise BinaryPacketError("invalid START byte")
    if data[-1] != END_BYTE:
        raise BinaryPacketError("invalid END byte")

    message_type_raw = data[1]
    seq = data[2]
    length = data[3]
    payload = data[4 : 4 + length]
    expected_total = 1 + 1 + 1 + 1 + length + 2 + 1
    if len(data) != expected_total:
        raise BinaryPacketError("LEN mismatch")

    crc_received = int.from_bytes(data[4 + length : 6 + length], "big")
    crc_payload = data[1 : 4 + length]
    if crc16_xmodem(crc_payload) != crc_received:
        raise BinaryPacketError("CRC mismatch")

    try:
        message_type = BinaryMessageType(message_type_raw)
    except ValueError as exc:
        raise BinaryPacketError(f"unknown TYPE 0x{message_type_raw:02X}") from exc

    return BinaryPacket(message_type=message_type, seq=seq, payload=payload)
