"""Binary protocol shared by the Acrylic Pan collector and PC monitor."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct
import zlib

MAGIC = b"APAN"
PROTOCOL_VERSION = 1
FRAME_HEADER = struct.Struct("<4sBBHIIH")
CRC = struct.Struct("<I")
EVENT_HEADER = struct.Struct("<IHHHH")
AI_RESULT_PAYLOAD = struct.Struct("<BBH8f")
MAX_PAYLOAD_SIZE = 4096


class ProtocolError(ValueError):
    """Raised when a received frame is malformed."""


class MessageType(IntEnum):
    HELLO = 0x01
    STATUS = 0x02
    START = 0x10
    STOP = 0x11
    SET_CONFIG = 0x12
    CAPTURE = 0x13
    AI_SELFTEST = 0x14
    EVENT_DATA = 0x20
    AI_RESULT = 0x21
    ACK = 0x70
    NACK = 0x71


@dataclass(frozen=True)
class Frame:
    message_type: MessageType
    sequence: int
    payload: bytes = b""
    flags: int = 0
    timestamp_us: int = 0


@dataclass(frozen=True)
class EventData:
    sample_rate_hz: int
    trigger_index: int
    peak_abs: int
    samples: tuple[int, ...]
    flags: int = 0
    sequence: int = 0
    timestamp_us: int = 0


@dataclass(frozen=True)
class AiResult:
    case_id: int
    predicted_class: int
    outputs: tuple[float, ...]
    sequence: int = 0
    timestamp_us: int = 0


def cobs_encode(data: bytes) -> bytes:
    """Encode one COBS packet without its trailing zero delimiter."""
    output = bytearray([0])
    code_index = 0
    code = 1
    for value in data:
        if value == 0:
            output[code_index] = code
            code_index = len(output)
            output.append(0)
            code = 1
        else:
            output.append(value)
            code += 1
            if code == 0xFF:
                output[code_index] = code
                code_index = len(output)
                output.append(0)
                code = 1
    output[code_index] = code
    return bytes(output)


def cobs_decode(data: bytes) -> bytes:
    """Decode one COBS packet without its trailing zero delimiter."""
    if not data:
        raise ProtocolError("empty COBS packet")
    output = bytearray()
    index = 0
    while index < len(data):
        code = data[index]
        if code == 0:
            raise ProtocolError("zero byte inside COBS packet")
        index += 1
        end = index + code - 1
        if end > len(data):
            raise ProtocolError("truncated COBS packet")
        output.extend(data[index:end])
        index = end
        if code != 0xFF and index < len(data):
            output.append(0)
    return bytes(output)


def encode_frame(frame: Frame) -> bytes:
    if len(frame.payload) > MAX_PAYLOAD_SIZE:
        raise ProtocolError("payload is too large")
    header = FRAME_HEADER.pack(
        MAGIC,
        PROTOCOL_VERSION,
        int(frame.message_type),
        frame.flags,
        frame.sequence,
        frame.timestamp_us,
        len(frame.payload),
    )
    body = header + frame.payload
    checksum = CRC.pack(zlib.crc32(body) & 0xFFFFFFFF)
    return cobs_encode(body + checksum) + b"\x00"


def decode_frame(packet: bytes) -> Frame:
    raw = cobs_decode(packet[:-1] if packet.endswith(b"\x00") else packet)
    minimum_size = FRAME_HEADER.size + CRC.size
    if len(raw) < minimum_size:
        raise ProtocolError("frame is too short")
    body, received_crc = raw[:-CRC.size], CRC.unpack(raw[-CRC.size:])[0]
    if zlib.crc32(body) & 0xFFFFFFFF != received_crc:
        raise ProtocolError("CRC mismatch")
    magic, version, kind, flags, sequence, timestamp_us, payload_size = (
        FRAME_HEADER.unpack(body[:FRAME_HEADER.size])
    )
    if magic != MAGIC:
        raise ProtocolError("invalid magic")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version: {version}")
    payload = body[FRAME_HEADER.size:]
    if payload_size != len(payload):
        raise ProtocolError("payload length mismatch")
    try:
        message_type = MessageType(kind)
    except ValueError as error:
        raise ProtocolError(f"unknown message type: {kind}") from error
    return Frame(message_type, sequence, payload, flags, timestamp_us)


def encode_event_payload(event: EventData) -> bytes:
    sample_count = len(event.samples)
    if sample_count == 0 or sample_count > 2048:
        raise ProtocolError("invalid sample count")
    if not 0 <= event.trigger_index < sample_count:
        raise ProtocolError("trigger index is outside the waveform")
    samples = struct.pack(f"<{sample_count}h", *event.samples)
    return EVENT_HEADER.pack(
        event.sample_rate_hz,
        sample_count,
        event.trigger_index,
        event.peak_abs,
        0,
    ) + samples


def decode_event(frame: Frame) -> EventData:
    if frame.message_type != MessageType.EVENT_DATA:
        raise ProtocolError("frame is not EVENT_DATA")
    if len(frame.payload) < EVENT_HEADER.size:
        raise ProtocolError("event header is truncated")
    sample_rate_hz, count, trigger_index, peak_abs, _ = EVENT_HEADER.unpack(
        frame.payload[:EVENT_HEADER.size]
    )
    sample_bytes = frame.payload[EVENT_HEADER.size:]
    if len(sample_bytes) != count * 2:
        raise ProtocolError("event sample length mismatch")
    if count == 0 or trigger_index >= count:
        raise ProtocolError("invalid event metadata")
    samples = struct.unpack(f"<{count}h", sample_bytes)
    return EventData(
        sample_rate_hz,
        trigger_index,
        peak_abs,
        samples,
        frame.flags,
        frame.sequence,
        frame.timestamp_us,
    )


def decode_ai_result(frame: Frame) -> AiResult:
    """Decode one deterministic dummy-model inference result.

    The payload is shared with the collector firmware as ``<BBH8f``:
    numeric test case, argmax class, reserved zero, and eight float32 scores.
    """
    if frame.message_type != MessageType.AI_RESULT:
        raise ProtocolError("frame is not AI_RESULT")
    if len(frame.payload) != AI_RESULT_PAYLOAD.size:
        raise ProtocolError("AI result payload length mismatch")
    case_id, predicted_class, reserved, *outputs = AI_RESULT_PAYLOAD.unpack(frame.payload)
    if reserved != 0:
        raise ProtocolError("unsupported AI result payload version")
    if predicted_class >= len(outputs):
        raise ProtocolError("AI result class is outside the output vector")
    return AiResult(
        case_id,
        predicted_class,
        tuple(outputs),
        frame.sequence,
        frame.timestamp_us,
    )


class FrameStreamDecoder:
    """Incrementally split a serial byte stream into validated frames."""

    def __init__(self, max_encoded_size: int = 8192) -> None:
        self._buffer = bytearray()
        self.max_encoded_size = max_encoded_size
        self.error_count = 0

    def feed(self, data: bytes) -> list[Frame]:
        self._buffer.extend(data)
        frames: list[Frame] = []
        while True:
            try:
                delimiter = self._buffer.index(0)
            except ValueError:
                break
            packet = bytes(self._buffer[:delimiter])
            del self._buffer[: delimiter + 1]
            if not packet:
                continue
            try:
                frames.append(decode_frame(packet))
            except ProtocolError:
                self.error_count += 1
        if len(self._buffer) > self.max_encoded_size:
            self._buffer.clear()
            self.error_count += 1
        return frames
