"""Durable calibration profiles on the machine serving the exhibition UI."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import uuid
import zlib


class CalibrationProfiles:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or os.environ.get(
            "APAN_CALIBRATION_DIR", str(Path.home() / ".acrylic_pan" / "calibrations")))

    def _path(self, profile_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", profile_id):
            raise ValueError("invalid calibration profile ID")
        return self.root / (profile_id + ".json")

    def save(self, name: str, beta: bytes, model_crc: int, kind: str, count: int) -> dict:
        if len(beta) != 32 * 60 * 2:
            raise ValueError("expected 32 x 60 bfloat16 weights")
        if kind not in ("quick", "precise") or not 0 <= count <= 65535:
            raise ValueError("invalid calibration metadata")
        if not name.strip() or len(name) > 120:
            raise ValueError("profile name must contain 1..120 characters")
        record = dict(version=1, id=uuid.uuid4().hex, name=name.strip(),
                      created_at=datetime.now(timezone.utc).isoformat(),
                      model_crc=model_crc, kind=kind, count=count,
                      beta_crc=zlib.crc32(beta), beta=base64.b64encode(beta).decode("ascii"))
        self.root.mkdir(parents=True, exist_ok=True)
        target = self._path(record["id"])
        temporary = target.with_suffix(".tmp")
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        return {k: v for k, v in record.items() if k != "beta"}

    def load(self, profile_id: str) -> dict:
        record = json.loads(self._path(profile_id).read_text(encoding="utf-8"))
        beta = base64.b64decode(record["beta"], validate=True)
        if (record.get("version") != 1 or record.get("id") != profile_id
                or len(beta) != 3840 or zlib.crc32(beta) != record["beta_crc"]):
            raise ValueError("damaged calibration profile")
        return {**record, "beta": beta}

    def list(self) -> list[dict]:
        records = []
        for path in self.root.glob("*.json"):
            record = self.load(path.stem)
            records.append({k: v for k, v in record.items() if k != "beta"})
        return sorted(records, key=lambda item: item["created_at"], reverse=True)
