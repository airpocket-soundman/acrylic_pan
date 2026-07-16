"""Crash-resistant event recording independent from the Tk GUI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import csv
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping
import uuid

import numpy as np

from .protocol import EventData


class RecordingError(RuntimeError):
    """Raised when an event cannot be persisted completely."""


@dataclass
class ReceiveStats:
    """Receiver counters, including uint32 sequence continuity."""

    frames_received: int = 0
    events_received: int = 0
    decoder_errors: int = 0
    missing_sequences: int = 0
    duplicate_sequences: int = 0
    out_of_order_sequences: int = 0
    events_saved: int = 0
    save_errors: int = 0
    _last_event_sequence: int | None = None

    def observe_frame(self) -> None:
        self.frames_received += 1

    def observe_event(self, sequence: int) -> None:
        self.events_received += 1
        sequence &= 0xFFFFFFFF
        if self._last_event_sequence is None:
            self._last_event_sequence = sequence
            return
        delta = (sequence - self._last_event_sequence) & 0xFFFFFFFF
        if delta == 0:
            self.duplicate_sequences += 1
        elif delta < 0x80000000:
            self.missing_sequences += delta - 1
            self._last_event_sequence = sequence
        else:
            self.out_of_order_sequences += 1


@dataclass(frozen=True)
class RecordedEvent:
    index: int
    sequence: int
    path: Path
    class_id: int | None
    received_at: str


class Recorder:
    """Write one session directory containing NPZ events and two manifests."""

    CSV_FIELDS = (
        "index", "sequence", "received_at", "file", "class_id",
        "sample_rate_hz", "sample_count", "trigger_index", "peak_abs",
        "flags", "timestamp_us",
    )

    def __init__(self, output_root: str | Path) -> None:
        self.output_root = Path(output_root)
        self.session_dir: Path | None = None
        self.session_id: str | None = None
        self._metadata: dict[str, Any] = {}
        self._event_count = 0
        self._closed = False

    @property
    def active(self) -> bool:
        return self.session_dir is not None and not self._closed

    @property
    def event_count(self) -> int:
        return self._event_count

    def begin_session(self, metadata: Mapping[str, Any] | None = None) -> Path:
        if self.active:
            return self.session_dir  # type: ignore[return-value]
        now = datetime.now(timezone.utc)
        self.session_id = f"{now.astimezone().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        self.session_dir = self.output_root / self.session_id
        self._closed = False
        self._event_count = 0
        (self.session_dir / "events").mkdir(parents=True, exist_ok=False)
        self._metadata = {
            "format": "acrylic-pan-session-v1",
            "session_id": self.session_id,
            "created_at": now.isoformat(),
            "closed_at": None,
            "event_count": 0,
            "user_metadata": dict(metadata or {}),
        }
        self._write_session_metadata()
        self._write_csv_header()
        (self.session_dir / "manifest.jsonl").touch()
        return self.session_dir

    def record_event(
        self,
        event: EventData,
        *,
        class_id: int | None = None,
        annotations: Mapping[str, Any] | None = None,
    ) -> RecordedEvent:
        if not self.active or self.session_dir is None:
            raise RecordingError("recording session has not been started")
        if class_id is not None and not 0 <= class_id < 8:
            raise RecordingError("class_id must be between 0 and 7")

        index = self._event_count + 1
        received_at = datetime.now(timezone.utc).isoformat()
        relative = Path("events") / f"event_{index:06d}_seq_{event.sequence:010d}.npz"
        destination = self.session_dir / relative
        record = {
            "index": index,
            "sequence": event.sequence,
            "received_at": received_at,
            "file": relative.as_posix(),
            "class_id": class_id,
            "sample_rate_hz": event.sample_rate_hz,
            "sample_count": len(event.samples),
            "trigger_index": event.trigger_index,
            "peak_abs": event.peak_abs,
            "flags": event.flags,
            "timestamp_us": event.timestamp_us,
            "annotations": dict(annotations or {}),
        }
        try:
            self._atomic_npz(destination, event, class_id, received_at)
            self._append_jsonl(record)
            self._append_csv(record)
            self._event_count = index
            self._metadata["event_count"] = self._event_count
            self._write_session_metadata()
        except Exception as error:
            raise RecordingError(f"could not save event {event.sequence}: {error}") from error
        return RecordedEvent(index, event.sequence, destination, class_id, received_at)

    def close(self) -> None:
        if not self.active:
            return
        self._metadata["closed_at"] = datetime.now(timezone.utc).isoformat()
        self._write_session_metadata()
        self._closed = True

    def _atomic_npz(
        self, destination: Path, event: EventData, class_id: int | None, received_at: str
    ) -> None:
        fd, temporary_name = tempfile.mkstemp(prefix=".event-", suffix=".npz.tmp", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                np.savez_compressed(
                    output,
                    samples=np.asarray(event.samples, dtype=np.int16),
                    sample_rate_hz=np.uint32(event.sample_rate_hz),
                    trigger_index=np.uint16(event.trigger_index),
                    peak_abs=np.uint16(event.peak_abs),
                    flags=np.uint16(event.flags),
                    sequence=np.uint32(event.sequence),
                    timestamp_us=np.uint32(event.timestamp_us),
                    class_id=np.int32(-1 if class_id is None else class_id),
                    received_at=np.asarray(received_at),
                )
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_name, destination)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def _write_session_metadata(self) -> None:
        assert self.session_dir is not None
        destination = self.session_dir / "session.json"
        temporary = destination.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(self._metadata, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)

    def _write_csv_header(self) -> None:
        assert self.session_dir is not None
        with (self.session_dir / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as output:
            csv.DictWriter(output, fieldnames=self.CSV_FIELDS).writeheader()

    def _append_csv(self, record: Mapping[str, Any]) -> None:
        assert self.session_dir is not None
        row = {name: record.get(name) for name in self.CSV_FIELDS}
        row["class_id"] = "" if row["class_id"] is None else row["class_id"]
        with (self.session_dir / "manifest.csv").open("a", encoding="utf-8-sig", newline="") as output:
            csv.DictWriter(output, fieldnames=self.CSV_FIELDS).writerow(row)
            output.flush()
            os.fsync(output.fileno())

    def _append_jsonl(self, record: Mapping[str, Any]) -> None:
        assert self.session_dir is not None
        with (self.session_dir / "manifest.jsonl").open("a", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            output.flush()
            os.fsync(output.fileno())


def make_demo_event(sequence: int = 1, seed: int = 7) -> EventData:
    """Generate a deterministic impact for GUI demos and automated tests."""
    sample_rate = 25_600
    count = 512
    trigger = 128
    sample_index = np.arange(count)
    seconds = sample_index / sample_rate
    envelope = np.where(sample_index >= trigger, np.exp(-(sample_index - trigger) / 95.0), 0.0)
    signal = 12_000 * envelope * (
        np.sin(2 * np.pi * 820 * seconds) + 0.45 * np.sin(2 * np.pi * 2_150 * seconds)
    )
    noise = np.random.default_rng(seed).normal(0, 80, count)
    samples = np.clip(signal + noise, -32768, 32767).astype(np.int16)
    return EventData(
        sample_rate,
        trigger,
        int(np.max(np.abs(samples.astype(np.int32)))),
        tuple(int(value) for value in samples),
        sequence=sequence,
    )
