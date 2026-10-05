from __future__ import annotations

import enum
import struct
import zlib
from dataclasses import dataclass


SOF = b"\xA5\x5A"
VERSION = 1
HEADER_FORMAT = "<2sBBHIHH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
CRC_SIZE = 4
MIN_FRAME_SIZE = HEADER_SIZE + CRC_SIZE
MAX_PAYLOAD_LEN = 4096


class SerialProtocolError(ValueError):
    pass


class MessageType(enum.IntEnum):
    HELLO = 0x01
    HEARTBEAT = 0x02
    TELEMETRY = 0x03
    DRIVER_STATE = 0x04
    LIMIT_STATE = 0x05
    FAULT = 0x06
    ACK = 0x07
    NACK = 0x08
    CONFIG_REPORT = 0x09
    UNKNOWN = 0xFF


@dataclass(frozen=True)
class SerialPacket:
    version: int
    msg_type: int
    seq_id: int
    timestamp_ms: int
    flags: int
    payload: bytes
    crc32: int

    @property
    def msg_type_name(self) -> str:
        try:
            return MessageType(self.msg_type).name
        except ValueError:
            return "UNKNOWN"


@dataclass(frozen=True)
class StreamDecodeResult:
    packets: list[SerialPacket]
    remainder: bytes
    errors: list[str]


def encode_packet(
    *,
    msg_type: MessageType | int,
    seq_id: int,
    timestamp_ms: int,
    payload: bytes = b"",
    flags: int = 0,
    version: int = VERSION,
) -> bytes:
    if len(payload) > MAX_PAYLOAD_LEN:
        raise SerialProtocolError("payload_len_exceeds_limit")
    msg_type_value = int(msg_type)
    header_without_crc = struct.pack(
        HEADER_FORMAT,
        SOF,
        int(version) & 0xFF,
        msg_type_value & 0xFF,
        int(seq_id) & 0xFFFF,
        int(timestamp_ms) & 0xFFFFFFFF,
        int(flags) & 0xFFFF,
        len(payload) & 0xFFFF,
    )
    crc = _crc_for(header_without_crc[2:] + payload)
    return header_without_crc + payload + struct.pack("<I", crc)


def decode_packet(frame: bytes) -> SerialPacket:
    if len(frame) < MIN_FRAME_SIZE:
        raise SerialProtocolError("partial_frame")
    sof, version, msg_type, seq_id, timestamp_ms, flags, payload_len = struct.unpack(HEADER_FORMAT, frame[:HEADER_SIZE])
    if sof != SOF:
        raise SerialProtocolError("bad_sof")
    if payload_len > MAX_PAYLOAD_LEN:
        raise SerialProtocolError("payload_len_exceeds_limit")
    expected_len = HEADER_SIZE + payload_len + CRC_SIZE
    if len(frame) < expected_len:
        raise SerialProtocolError("partial_frame")
    if len(frame) > expected_len:
        raise SerialProtocolError("trailing_bytes")
    payload = frame[HEADER_SIZE:HEADER_SIZE + payload_len]
    expected_crc = struct.unpack("<I", frame[HEADER_SIZE + payload_len:expected_len])[0]
    calculated_crc = _crc_for(frame[2:HEADER_SIZE] + payload)
    if expected_crc != calculated_crc:
        raise SerialProtocolError("crc_mismatch")
    return SerialPacket(
        version=version,
        msg_type=msg_type,
        seq_id=seq_id,
        timestamp_ms=timestamp_ms,
        flags=flags,
        payload=payload,
        crc32=expected_crc,
    )


def validate_crc(frame: bytes) -> bool:
    try:
        decode_packet(frame)
        return True
    except SerialProtocolError:
        return False


def decode_stream(buffer: bytes) -> StreamDecodeResult:
    packets: list[SerialPacket] = []
    errors: list[str] = []
    cursor = 0
    length = len(buffer)
    while cursor < length:
        start = buffer.find(SOF, cursor)
        if start < 0:
            return StreamDecodeResult(packets=packets, remainder=b"", errors=errors)
        if start > cursor:
            errors.append("noise_discarded")
        if length - start < MIN_FRAME_SIZE:
            return StreamDecodeResult(packets=packets, remainder=buffer[start:], errors=errors)
        try:
            _, _, _, _, _, _, payload_len = struct.unpack(HEADER_FORMAT, buffer[start:start + HEADER_SIZE])
        except struct.error:
            return StreamDecodeResult(packets=packets, remainder=buffer[start:], errors=errors)
        if payload_len > MAX_PAYLOAD_LEN:
            errors.append("payload_len_exceeds_limit")
            cursor = start + 1
            continue
        frame_len = HEADER_SIZE + payload_len + CRC_SIZE
        if length - start < frame_len:
            return StreamDecodeResult(packets=packets, remainder=buffer[start:], errors=errors)
        frame = buffer[start:start + frame_len]
        try:
            packets.append(decode_packet(frame))
            cursor = start + frame_len
        except SerialProtocolError as exc:
            errors.append(str(exc))
            cursor = start + 1
    return StreamDecodeResult(packets=packets, remainder=b"", errors=errors)


def message_type_name(msg_type: int) -> str:
    try:
        return MessageType(msg_type).name
    except ValueError:
        return "UNKNOWN"


def _crc_for(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF
