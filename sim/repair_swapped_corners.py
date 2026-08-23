"""Swap two guided point annotations after a physical collection mix-up."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil


def swap_points(session_dir: Path, class_id: int, first_point: int, second_point: int,
                expected_count: int) -> dict[int, int]:
    directory = Path(session_dir).resolve()
    metadata = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    targets = metadata["user_metadata"]["collection_plan"]["targets"]
    canonical = {}
    for target_index, target in enumerate(targets):
        if int(target["class_id"]) == class_id and int(target["point_id"]) in (
            first_point, second_point
        ):
            canonical[int(target["point_id"])] = (target_index, target)
    if set(canonical) != {first_point, second_point}:
        raise ValueError("both points were not found in the session collection plan")

    manifest = directory / "manifest.jsonl"
    backup = directory / "manifest.jsonl.bak-before-point-swap"
    if backup.exists():
        if backup.read_bytes() != manifest.read_bytes():
            raise ValueError("backup already exists and differs from the current manifest")
    else:
        shutil.copy2(manifest, backup)
    rows = [
        json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    changed = {first_point: 0, second_point: 0}
    destinations = {first_point: second_point, second_point: first_point}
    for row in rows:
        annotations = row.get("annotations") or {}
        source_point = annotations.get("target_point_id")
        if row.get("class_id") != class_id or source_point not in destinations:
            continue
        destination_index, destination = canonical[destinations[source_point]]
        offset = destination["offset"]
        annotations.update({
            "target_index": destination_index,
            "target_point_id": int(destination["point_id"]),
            "target_point_name": str(destination["point_name"]),
            "target_x_mm": float(destination["x_mm"]),
            "target_y_mm": float(destination["y_mm"]),
            "offset_x_mm": float(offset["x_mm"]),
            "offset_y_mm": float(offset["y_mm"]),
        })
        changed[int(source_point)] += 1
    if changed != {first_point: expected_count, second_point: expected_count}:
        raise ValueError(f"unexpected point counts: {changed}")

    temporary = manifest.with_suffix(".jsonl.tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8", newline="\n",
    )
    os.replace(temporary, manifest)
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_dir", type=Path)
    parser.add_argument("--class-id", type=int, required=True)
    parser.add_argument("--first-point", type=int, required=True)
    parser.add_argument("--second-point", type=int, required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    args = parser.parse_args()
    changed = swap_points(
        args.session_dir, args.class_id, args.first_point, args.second_point, args.expected_count
    )
    print(json.dumps({"changed": changed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
