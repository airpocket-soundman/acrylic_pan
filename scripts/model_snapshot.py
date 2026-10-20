"""Save or restore the complete model set under a name, e.g. one tied to a board support condition.

    python scripts/model_snapshot.py save support-1point --note "board held at one point"
    python scripts/model_snapshot.py restore support-1point

A snapshot lives in models/<name>/ and holds
  firmware-generated/  the model headers compiled into the board firmware,
  artifacts/           the PC-side models (git-ignored because they are large),
  firmware/            the firmware image that was running with them, when one is given,
  MANIFEST.md          what was saved, with SHA-256 hashes and the training sessions.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
GENERATED = ROOT / "firmware" / "AcrylicPanCollector" / "generated"
HEADERS = ("apan_12class_model.h", "apan_position_probability_model.h",
           "apan_calibration_prior.h", "apan_calibration_golden.h")
ARTIFACT_DIRS = ("pc_position_runtime", "device_position_probability_400x300x5",
                 "pc_position_runtime_400x300x5", "pc_area_classifier_400x300x5")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*arguments: str) -> str:
    try:
        return subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def training_sessions(session_ids: list[str] | None) -> list[str]:
    if session_ids:
        return session_ids
    sys.path.insert(0, str(ROOT))
    try:
        from sim.pc_position_grid_runtime import DEFAULT_SESSION_IDS
        return list(DEFAULT_SESSION_IDS)
    except Exception:  # the manifest is still useful without it
        return []


def save(name: str, note: str, firmware: Path | None, artifacts: tuple[str, ...],
         session_ids: list[str] | None, sessions_dir: str) -> None:
    target = MODELS / name
    if target.exists():
        raise SystemExit(f"{target} already exists; choose another name or remove it first")
    rows: list[tuple[str, int, str]] = []
    (target / "firmware-generated").mkdir(parents=True)
    for header in HEADERS:
        source = GENERATED / header
        if source.is_file():
            shutil.copy2(source, target / "firmware-generated" / header)
    for directory in artifacts:
        source = ROOT / "artifacts" / directory
        if source.is_dir():
            shutil.copytree(source, target / "artifacts" / directory)
    if firmware is not None:
        (target / "firmware").mkdir()
        shutil.copy2(firmware, target / "firmware" / firmware.name)
        elf = firmware.with_suffix(".elf")
        if elf.is_file():
            shutil.copy2(elf, target / "firmware" / elf.name)
    for path in sorted(p for p in target.rglob("*") if p.is_file()):
        rows.append((path.relative_to(target).as_posix(), path.stat().st_size, sha256(path)))
    sessions = training_sessions(session_ids)
    lines = [
        f"# モデルのスナップショット: {name}", "",
        f"- 保存日時: {datetime.now():%Y-%m-%d %H:%M}",
        f"- 説明: {note or '(なし)'}",
        f"- Git: `{git('rev-parse', 'HEAD')}`(未コミットの変更を含むことがある)", "",
        f"## 学習に使ったセッション(`{sessions_dir}`)", "",
        *([f"- `{session}`" for session in sessions] or ["- (取得できませんでした)"]), "",
        "## ファイル", "", "| ファイル | バイト | SHA-256 |", "|---|---:|---|",
        *[f"| `{path}` | {size:,} | `{digest[:16]}…` |" for path, size, digest in rows], "",
        "## 戻し方", "", f"```\npython scripts/model_snapshot.py restore {name}\n```", "",
        "ファームのモデルヘッダーとPC側のモデルを元の場所へ戻す。ボードのファームは、"
        "戻したあとで、ビルドして書き込む(`firmware/`に当時のイメージがあれば、それを書き込める)。", "",
    ]
    (target / "MANIFEST.md").write_text("\n".join(lines), encoding="utf-8")
    total = sum(size for _, size, _ in rows)
    print(f"saved {len(rows)} files ({total / 1e6:.1f} MB) to {target}")


def restore(name: str) -> None:
    source = MODELS / name
    if not source.is_dir():
        raise SystemExit(f"{source} does not exist")
    for header in (source / "firmware-generated").glob("*.h"):
        shutil.copy2(header, GENERATED / header.name)
    for directory in (source / "artifacts").glob("*"):
        destination = ROOT / "artifacts" / directory.name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(directory, destination)
    print(f"restored {name}; rebuild and flash the firmware to put the models on the board")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    save_parser = commands.add_parser("save")
    save_parser.add_argument("name")
    save_parser.add_argument("--note", default="")
    save_parser.add_argument("--firmware", type=Path, help="firmware .hex that runs with these models")
    save_parser.add_argument("--artifacts", nargs="+", default=list(ARTIFACT_DIRS),
                             help="folders under artifacts/ to save (default: the one-point PC and device models)")
    save_parser.add_argument("--session-ids", default="", help="comma list of the training sessions to record")
    save_parser.add_argument("--sessions-dir", default="data/raw/sessions", help="where those sessions are")
    commands.add_parser("restore").add_argument("name")
    arguments = parser.parse_args()
    if arguments.command == "save":
        ids = [item.strip() for item in arguments.session_ids.split(',') if item.strip()]
        save(arguments.name, arguments.note, arguments.firmware, tuple(arguments.artifacts), ids or None,
             arguments.sessions_dir)
    else:
        restore(arguments.name)


if __name__ == "__main__":
    main()
