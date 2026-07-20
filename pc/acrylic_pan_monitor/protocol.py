"""Binary protocol shared by the Acrylic Pan collector and PC monitor."""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from enum import IntEnum
import struct
import zlib
import time

MAGIC = b"APAN"
PROTOCOL_VERSION = 1
FRAME_HEADER = struct.Struct("<4sBBHIIH")
CRC = struct.Struct("<I")
EVENT_HEADER = struct.Struct("<IHHHH")
AI_RESULT_PAYLOAD = struct.Struct("<BBH8f")
AI_RESULT_12_PAYLOAD = struct.Struct("<BBH12f")
EVENT_CHUNK_HEADER = struct.Struct("<IHHIHHHH")
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
    SET_MODE = 0x15
    EVENT_DATA = 0x20
    AI_RESULT = 0x21
    INFERENCE_EVENT = 0x22
    EVENT_CHUNK = 0x23
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


@dataclass(frozen=True)
class InferenceEvent:
    event: EventData
    result: AiResult


@dataclass(frozen=True)
class EventChunk:
    event_id: int
    chunk_index: int
    chunk_count: int
    sample_rate_hz: int
    total_samples: int
    trigger_index: int
    peak_abs: int
    samples: tuple[int, ...]
    transport_sequence: int = 0
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
    formats = {
        AI_RESULT_PAYLOAD.size: AI_RESULT_PAYLOAD,
        AI_RESULT_12_PAYLOAD.size: AI_RESULT_12_PAYLOAD,
    }
    payload_format = formats.get(len(frame.payload))
    if payload_format is None:
        raise ProtocolError("AI result payload length mismatch")
    case_id, predicted_class, reserved, *outputs = payload_format.unpack(frame.payload)
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


def decode_inference_event(frame: Frame) -> InferenceEvent:
    """Decode one atomic live classification result and its source waveform."""
    if frame.message_type != MessageType.INFERENCE_EVENT:
        raise ProtocolError("frame is not INFERENCE_EVENT")
    minimum = EVENT_HEADER.size + AI_RESULT_PAYLOAD.size
    if len(frame.payload) < minimum:
        raise ProtocolError("inference event header is truncated")
    event_header = frame.payload[:EVENT_HEADER.size]
    sample_rate_hz, count, trigger_index, peak_abs, event_reserved = EVENT_HEADER.unpack(
        event_header
    )
    sample_size = count * 2
    ai_size = len(frame.payload) - EVENT_HEADER.size - sample_size
    formats = {
        AI_RESULT_PAYLOAD.size: AI_RESULT_PAYLOAD,
        AI_RESULT_12_PAYLOAD.size: AI_RESULT_12_PAYLOAD,
    }
    payload_format = formats.get(ai_size)
    if payload_format is None:
        raise ProtocolError("unsupported inference output count")
    ai_start = EVENT_HEADER.size
    ai_end = ai_start + ai_size
    case_id, predicted_class, ai_reserved, *outputs = payload_format.unpack(
        frame.payload[ai_start:ai_end]
    )
    sample_bytes = frame.payload[ai_end:]
    if event_reserved != 0 or ai_reserved != 0 or case_id != 0xFF:
        raise ProtocolError("unsupported inference event payload version")
    if count == 0 or trigger_index >= count or len(sample_bytes) != count * 2:
        raise ProtocolError("invalid inference event waveform")
    if predicted_class >= len(outputs):
        raise ProtocolError("inference event class is outside the output vector")
    samples = struct.unpack(f"<{count}h", sample_bytes)
    event = EventData(
        sample_rate_hz, trigger_index, peak_abs, samples,
        frame.flags, frame.sequence, frame.timestamp_us,
    )
    result = AiResult(
        case_id, predicted_class, tuple(outputs), frame.sequence, frame.timestamp_us
    )
    return InferenceEvent(event, result)


def decode_event_chunk(frame: Frame) -> EventChunk:
    """Decode one independently CRC-protected part of a long acquisition."""
    if frame.message_type != MessageType.EVENT_CHUNK:
        raise ProtocolError("frame is not EVENT_CHUNK")
    if len(frame.payload) < EVENT_CHUNK_HEADER.size:
        raise ProtocolError("event chunk header is truncated")
    (event_id, chunk_index, chunk_count, sample_rate_hz, total_samples,
     trigger_index, peak_abs, chunk_samples) = EVENT_CHUNK_HEADER.unpack(
        frame.payload[:EVENT_CHUNK_HEADER.size]
    )
    sample_bytes = frame.payload[EVENT_CHUNK_HEADER.size:]
    if chunk_count == 0 or chunk_count > 16 or chunk_index >= chunk_count:
        raise ProtocolError("invalid event chunk index")
    if total_samples == 0 or total_samples > 8192 or trigger_index >= total_samples:
        raise ProtocolError("invalid event chunk metadata")
    offset = chunk_index * 512
    expected = min(512, total_samples - offset) if offset < total_samples else 0
    if chunk_samples != expected or len(sample_bytes) != chunk_samples * 2:
        raise ProtocolError("event chunk sample length mismatch")
    if chunk_count != (total_samples + 511) // 512:
        raise ProtocolError("event chunk count does not match total samples")
    samples = struct.unpack(f"<{chunk_samples}h", sample_bytes)
    return EventChunk(
        event_id, chunk_index, chunk_count, sample_rate_hz, total_samples,
        trigger_index, peak_abs, samples, frame.sequence, frame.timestamp_us,
    )


class EventAssembler:
    """Reassemble long events without ever exposing or saving partial data."""

    def __init__(self, timeout_seconds: float = 2.0, max_inflight: int = 4,
                 clock=time.monotonic) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_inflight = max_inflight
        self.clock = clock
        self._partial: dict[int, dict] = {}
        self._completed = deque(maxlen=256)
        self._completed_set: set[int] = set()
        self.duplicates = 0
        self.conflicts = 0
        self.timed_out = 0
        self.completed = 0

    @property
    def inflight(self) -> int:
        return len(self._partial)

    @property
    def progress(self) -> dict[str, int] | None:
        if not self._partial:
            return None
        event_id, partial = min(
            self._partial.items(), key=lambda item: item[1]["created"]
        )
        return {
            "event_id": event_id,
            "received_chunks": len(partial["chunks"]),
            "total_chunks": partial["metadata"][0],
        }

    def reset(self) -> None:
        self._partial.clear()
        self._completed.clear()
        self._completed_set.clear()

    def expire(self) -> list[int]:
        now = self.clock()
        expired = [event_id for event_id, partial in self._partial.items()
                   if now - partial["created"] > self.timeout_seconds]
        for event_id in expired:
            del self._partial[event_id]
            self.timed_out += 1
        return expired

    def feed(self, chunk: EventChunk) -> EventData | None:
        self.expire()
        if chunk.event_id in self._completed_set:
            self.duplicates += 1
            return None
        metadata = (
            chunk.chunk_count, chunk.sample_rate_hz, chunk.total_samples,
            chunk.trigger_index, chunk.peak_abs,
        )
        partial = self._partial.get(chunk.event_id)
        if partial is None:
            if len(self._partial) >= self.max_inflight:
                oldest = min(self._partial, key=lambda key: self._partial[key]["created"])
                del self._partial[oldest]
                self.timed_out += 1
            partial = {"metadata": metadata, "chunks": {},
                       "created": self.clock(), "timestamp_us": chunk.timestamp_us}
            self._partial[chunk.event_id] = partial
        elif partial["metadata"] != metadata:
            del self._partial[chunk.event_id]
            self.conflicts += 1
            raise ProtocolError("event chunk metadata conflict")
        existing = partial["chunks"].get(chunk.chunk_index)
        if existing is not None:
            if existing != chunk.samples:
                del self._partial[chunk.event_id]
                self.conflicts += 1
                raise ProtocolError("conflicting duplicate event chunk")
            self.duplicates += 1
            return None
        partial["chunks"][chunk.chunk_index] = chunk.samples
        if len(partial["chunks"]) != chunk.chunk_count:
            return None
        samples = tuple(value for index in range(chunk.chunk_count)
                        for value in partial["chunks"][index])
        del self._partial[chunk.event_id]
        if len(samples) != chunk.total_samples:
            self.conflicts += 1
            raise ProtocolError("assembled event sample count mismatch")
        if len(self._completed) == self._completed.maxlen:
            self._completed_set.discard(self._completed[0])
        self._completed.append(chunk.event_id)
        self._completed_set.add(chunk.event_id)
        self.completed += 1
        return EventData(
            chunk.sample_rate_hz, chunk.trigger_index, chunk.peak_abs, samples,
            0, chunk.event_id, partial["timestamp_us"],
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
