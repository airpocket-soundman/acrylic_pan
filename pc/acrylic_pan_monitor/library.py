"""Read-and-delete access to recorded sessions on disk.

The Recorder owns writing. This module owns browsing and deletion, so past
sessions stay usable without a live Recorder instance.

``manifest.jsonl`` is the canonical index: ``sim.solist_dataset`` rejects a
session whose ``session.json`` ``event_count`` disagrees with the manifest row
count, so every deletion has to update the NPZ, both manifests, and the session
metadata together.
"""

from __future__ import annotations

from dataclasses import dataclass
import csv
import io
import json
import os
from pathlib import Path
import re
from typing import Any

import numpy as np

from .protocol import EventData

SESSION_ID_PATTERN = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{8}$")
SESSION_FORMAT = "acrylic-pan-session-v1"


class LibraryError(RuntimeError):
    """Raised when a stored session cannot be read or modified."""


@dataclass(frozen=True)
class StoredEvent:
    """One manifest row joined with its resolved NPZ path."""

    index: int
    sequence: int
    received_at: str
    path: Path
    class_id: int | None
    sample_rate_hz: int
    sample_count: int
    trigger_index: int
    peak_abs: int
    annotations: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "sequence": self.sequence,
            "received_at": self.received_at,
            "file": self.path.name,
            "class_id": self.class_id,
            "area": None if self.class_id is None else self.class_id + 1,
            "sample_rate_hz": self.sample_rate_hz,
            "sample_count": self.sample_count,
            "trigger_index": self.trigger_index,
            "peak_abs": self.peak_abs,
            "annotations": self.annotations,
            "exists": self.path.is_file(),
        }


class Library:
    """Browse and delete Recorder v1 sessions under one output root."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    # -- paths -------------------------------------------------------------

    def session_dir(self, session_id: str) -> Path:
        """Resolve one session directory, rejecting traversal attempts.

        ``session_id`` arrives from HTTP, so it is matched against the exact
        Recorder id format rather than merely sanitized.
        """
        if not SESSION_ID_PATTERN.match(session_id or ""):
            raise LibraryError(f"invalid session id: {session_id!r}")
        directory = (self.root / session_id).resolve()
        if directory.parent != self.root:
            raise LibraryError("session path escapes the output root")
        if not (directory / "session.json").is_file():
            raise LibraryError(f"session not found: {session_id}")
        return directory

    # -- reading -----------------------------------------------------------

    def list_sessions(self) -> list[dict[str, Any]]:
        """Summarize every readable session, newest first.

        An unreadable session is reported with an ``error`` instead of raising,
        so one damaged directory cannot hide the rest of the archive.
        """
        if not self.root.is_dir():
            return []
        summaries: list[dict[str, Any]] = []
        for directory in sorted(self.root.iterdir(), reverse=True):
            if not (directory / "session.json").is_file():
                continue
            try:
                summaries.append(self._summarize(directory))
            except (LibraryError, OSError, ValueError) as error:
                summaries.append({
                    "session_id": directory.name,
                    "error": str(error),
                    "event_count": 0,
                    "events": [],
                })
        return summaries

    def _summarize(self, directory: Path) -> dict[str, Any]:
        metadata = self._read_metadata(directory)
        rows = self._read_manifest(directory)
        classes = sorted({row["class_id"] for row in rows if row.get("class_id") is not None})
        user_metadata = metadata.get("user_metadata") or {}
        return {
            "session_id": metadata.get("session_id", directory.name),
            "created_at": metadata.get("created_at"),
            "closed_at": metadata.get("closed_at"),
            "event_count": len(rows),
            "declared_event_count": metadata.get("event_count"),
            "mode": user_metadata.get("mode"),
            "position_pattern": (user_metadata.get("collection_plan") or {}).get("position_pattern"),
            "class_ids": classes,
            "consistent": metadata.get("event_count") == len(rows),
        }

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        directory = self.session_dir(session_id)
        return [event.as_dict() for event in self._stored_events(directory)]

    def _stored_events(self, directory: Path) -> list[StoredEvent]:
        events: list[StoredEvent] = []
        for row in self._read_manifest(directory):
            events.append(StoredEvent(
                index=int(row["index"]),
                sequence=int(row.get("sequence", 0)),
                received_at=str(row.get("received_at", "")),
                path=self._event_path(directory, row),
                class_id=row.get("class_id"),
                sample_rate_hz=int(row.get("sample_rate_hz", 0)),
                sample_count=int(row.get("sample_count", 0)),
                trigger_index=int(row.get("trigger_index", 0)),
                peak_abs=int(row.get("peak_abs", 0)),
                annotations=dict(row.get("annotations") or {}),
            ))
        return events

    def _event_path(self, directory: Path, row: dict[str, Any]) -> Path:
        relative = str(row.get("file", ""))
        if not relative:
            raise LibraryError(f"{directory.name}: manifest row has no file")
        path = (directory / relative).resolve()
        try:
            path.relative_to(directory)
        except ValueError as error:
            raise LibraryError(f"{directory.name}: event path escapes session") from error
        return path

    def load_event(self, session_id: str, index: int) -> tuple[EventData, dict[str, Any]]:
        """Rebuild one stored waveform as an EventData plus its manifest row."""
        directory = self.session_dir(session_id)
        stored = self._find(directory, index)
        if not stored.path.is_file():
            raise LibraryError(f"event file is missing: {stored.path.name}")
        try:
            with np.load(stored.path, allow_pickle=False) as saved:
                samples = np.asarray(saved["samples"], dtype=np.int16)
                event = EventData(
                    sample_rate_hz=int(saved["sample_rate_hz"]),
                    trigger_index=int(saved["trigger_index"]),
                    peak_abs=int(saved["peak_abs"]),
                    samples=tuple(int(value) for value in samples),
                    flags=int(saved["flags"]),
                    sequence=int(saved["sequence"]),
                    timestamp_us=int(saved["timestamp_us"]),
                )
        except (OSError, ValueError, KeyError) as error:
            raise LibraryError(f"could not read {stored.path.name}: {error}") from error
        return event, stored.as_dict()

    def _find(self, directory: Path, index: int) -> StoredEvent:
        for stored in self._stored_events(directory):
            if stored.index == index:
                return stored
        raise LibraryError(f"event {index} is not in {directory.name}")

    # -- deleting ----------------------------------------------------------

    def delete_event(self, session_id: str, index: int) -> dict[str, Any]:
        """Remove one event from both manifests, the metadata, and disk.

        The manifests are rewritten before the NPZ is unlinked. A crash in
        between leaves an orphan file, which readers ignore; the reverse order
        would leave a manifest row pointing at a file that no longer exists and
        would make the whole session unloadable.
        """
        directory = self.session_dir(session_id)
        stored = self._find(directory, index)
        rows = [row for row in self._read_manifest(directory) if int(row["index"]) != index]

        self._write_manifest(directory, rows)
        self._write_csv(directory, rows)
        metadata = self._read_metadata(directory)
        metadata["event_count"] = len(rows)
        self._write_metadata(directory, metadata)
        try:
            stored.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise LibraryError(f"manifests updated but {stored.path.name} remains: {error}") from error
        return {"session_id": session_id, "index": index, "event_count": len(rows)}

    def delete_session(self, session_id: str) -> dict[str, Any]:
        """Delete one whole session directory and everything inside it."""
        directory = self.session_dir(session_id)
        removed = 0
        events_dir = directory / "events"
        if events_dir.is_dir():
            for path in events_dir.iterdir():
                if path.is_file():
                    path.unlink()
                    removed += 1
            events_dir.rmdir()
        for name in ("session.json", "manifest.jsonl", "manifest.csv"):
            path = directory / name
            if path.is_file():
                path.unlink()
        for leftover in list(directory.iterdir()):
            if leftover.is_file():
                leftover.unlink()
        directory.rmdir()
        return {"session_id": session_id, "removed_events": removed}

    # -- file helpers ------------------------------------------------------

    def _read_metadata(self, directory: Path) -> dict[str, Any]:
        try:
            metadata = json.loads((directory / "session.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise LibraryError(f"{directory.name}: unreadable session.json") from error
        if metadata.get("format") != SESSION_FORMAT:
            raise LibraryError(f"{directory.name}: unsupported session format")
        return metadata

    def _read_manifest(self, directory: Path) -> list[dict[str, Any]]:
        manifest = directory / "manifest.jsonl"
        if not manifest.is_file():
            raise LibraryError(f"{directory.name}: missing manifest.jsonl")
        try:
            lines = manifest.read_text(encoding="utf-8").splitlines()
            return [json.loads(line) for line in lines if line.strip()]
        except (OSError, ValueError) as error:
            raise LibraryError(f"{directory.name}: unreadable manifest.jsonl") from error

    def _write_metadata(self, directory: Path, metadata: dict[str, Any]) -> None:
        self._atomic_write(
            directory / "session.json",
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_manifest(self, directory: Path, rows: list[dict[str, Any]]) -> None:
        body = "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows
        )
        self._atomic_write(directory / "manifest.jsonl", body, encoding="utf-8")

    def _write_csv(self, directory: Path, rows: list[dict[str, Any]]) -> None:
        from .recorder import Recorder

        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=Recorder.CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            values = {name: row.get(name) for name in Recorder.CSV_FIELDS}
            values["class_id"] = "" if values["class_id"] is None else values["class_id"]
            writer.writerow(values)
        self._atomic_write(directory / "manifest.csv", buffer.getvalue(), encoding="utf-8-sig")

    def _atomic_write(self, destination: Path, text: str, encoding: str) -> None:
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        with temporary.open("w", encoding=encoding, newline="") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
