"""Guided on-site calibration of the 12-area model on the Solist-AI board.

The firmware learns each guided hit with a float32 OS-ELM (see
docs/odl-calibration-experiment-20260927.md).  This tool checks every area
once, trains ``--rounds`` passes over all areas, checks again, and keeps the
new beta only when the check accuracy did not drop.

    python -m pc.acrylic_pan_monitor.calibrate --port COM3 --rounds 5
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from .protocol import (
    CALIBRATION_CHECK_ONLY,
    CalibrationOp,
    CalibrationResult,
    CalibrationStatus,
    Frame,
    FrameStreamDecoder,
    MessageType,
    ProtocolError,
    decode_calibration_ack,
    decode_calibration_result,
    encode_calibration_request,
    encode_frame,
)

AREA_COUNT = 12
COLUMNS = 4
CALIBRATION_MODE = 4
NACK_REASONS = {1: "busy", 2: "unsupported", 3: "invalid state", 4: "failed"}


class Transport(Protocol):
    def write(self, data: bytes) -> object: ...
    def read(self, size: int) -> bytes: ...


class DeviceError(RuntimeError):
    """The firmware rejected a request or did not answer."""


def area_label(area: int) -> str:
    """1-based area number with its grid position (row 1 is the top)."""
    return f"エリア{area + 1:2d}（{area // COLUMNS + 1}行{area % COLUMNS + 1}列）"


class CalibrationClient:
    def __init__(self, transport: Transport, response_timeout: float = 3.0) -> None:
        self.transport = transport
        self.response_timeout = response_timeout
        self.decoder = FrameStreamDecoder()
        self.sequence = 1
        self.pending: list[Frame] = []

    def _next_frame(self, deadline: float | None) -> Frame:
        while not self.pending:
            if deadline is not None and time.monotonic() > deadline:
                raise DeviceError("no response from the firmware")
            self.pending.extend(self.decoder.feed(self.transport.read(4096)))
        return self.pending.pop(0)

    def _request(self, packet: bytes, sequence: int) -> Frame:
        self.transport.write(packet)
        deadline = time.monotonic() + self.response_timeout
        while True:
            frame = self._next_frame(deadline)
            if frame.sequence != sequence:
                continue
            if frame.message_type == MessageType.NACK:
                reason = frame.payload[1] if len(frame.payload) > 1 else 0
                raise DeviceError(f"request rejected: {NACK_REASONS.get(reason, reason)}")
            return frame

    def set_calibration_mode(self) -> None:
        sequence = self._take_sequence()
        frame = self._request(
            encode_frame(Frame(MessageType.SET_MODE, sequence, bytes([CALIBRATION_MODE]))),
            sequence,
        )
        if frame.message_type != MessageType.ACK or frame.payload[-1:] != bytes([CALIBRATION_MODE]):
            raise DeviceError("firmware does not support calibration mode")

    def calibration(self, operation: CalibrationOp, argument: int = 0) -> CalibrationStatus:
        sequence = self._take_sequence()
        frame = self._request(encode_calibration_request(sequence, operation, argument), sequence)
        return decode_calibration_ack(frame)

    def hit(self, target: int | None) -> CalibrationResult:
        """Arm one hit; ``None`` only checks the prediction without learning."""
        self.calibration(CalibrationOp.ARM,
                         CALIBRATION_CHECK_ONLY if target is None else target)
        while True:
            frame = self._next_frame(None)
            if frame.message_type == MessageType.NACK:
                raise DeviceError("the hit could not be inferred")
            if frame.message_type == MessageType.CALIBRATION_RESULT:
                return decode_calibration_result(frame)

    def _take_sequence(self) -> int:
        sequence = self.sequence
        self.sequence += 1
        return sequence


@dataclass
class CalibrationReport:
    rounds: int
    initial_source: str = ""
    before_correct: int = 0
    after_correct: int = 0
    training_correct_before_update: int = 0
    training_hits: int = 0
    max_train_ms: float = 0.0
    decision: str = ""
    hits: list[dict] = field(default_factory=list)


def _check_pass(client: CalibrationClient, prompt: Callable[[str], None],
                report: CalibrationReport, phase: str) -> int:
    correct = 0
    for area in range(AREA_COUNT):
        prompt(f"[{phase}] {area_label(area)} を1回叩いてください")
        result = client.hit(None)
        correct += int(result.predicted_class == area)
        report.hits.append({"phase": phase, "area": area,
                            "predicted": result.predicted_class})
        mark = "○" if result.predicted_class == area else f"× → {area_label(result.predicted_class)}"
        prompt(f"    推定 {mark}")
    return correct


def run_calibration(client: CalibrationClient, rounds: int,
                    prompt: Callable[[str], None] = print,
                    confirm: Callable[[str], bool] | None = None) -> CalibrationReport:
    """Check, train ``rounds`` passes over every area, check again, then commit or discard."""
    if rounds < 1:
        raise ValueError("rounds must be positive")
    report = CalibrationReport(rounds)
    client.set_calibration_mode()
    report.initial_source = client.calibration(CalibrationOp.STATUS).source.name.lower()
    prompt(f"現在のモデル: {report.initial_source}")
    try:
        report.before_correct = _check_pass(client, prompt, report, "校正前の確認")
        client.calibration(CalibrationOp.BEGIN)
        for round_index in range(rounds):
            for area in range(AREA_COUNT):
                prompt(f"[学習 {round_index + 1}/{rounds}] {area_label(area)} を叩いてください")
                result = client.hit(area)
                if not result.trained:
                    raise DeviceError("the firmware could not learn the hit")
                report.training_hits += 1
                report.training_correct_before_update += int(result.predicted_class == area)
                report.max_train_ms = max(report.max_train_ms, result.train_us / 1000.0)
                report.hits.append({"phase": "train", "area": area,
                                    "predicted": result.predicted_class,
                                    "train_us": result.train_us})
                prompt(f"    学習済み {result.work_count}打（更新 {result.train_us / 1000:.0f} ms）")
        report.after_correct = _check_pass(client, prompt, report, "校正後の確認")
    except BaseException:
        # Leave the board on its previous beta whatever went wrong.
        try:
            client.calibration(CalibrationOp.DISCARD)
        except (DeviceError, ProtocolError, OSError):
            pass
        raise

    summary = (f"確認打の正解: 校正前 {report.before_correct}/{AREA_COUNT}、"
               f"校正後 {report.after_correct}/{AREA_COUNT}")
    prompt(summary)
    keep = report.after_correct >= report.before_correct
    if confirm is not None:
        keep = confirm(f"{summary}。この校正を保存しますか？")
    if keep:
        status = client.calibration(CalibrationOp.COMMIT)
        report.decision = "committed"
        prompt(f"保存しました（{status.saved_count}打分、再起動後も有効）")
    else:
        status = client.calibration(CalibrationOp.DISCARD)
        report.decision = "discarded"
        prompt(f"破棄しました（使用中のモデル: {status.source.name.lower()}）")
    return report


def main() -> None:
    import serial

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True)
    parser.add_argument("--rounds", type=int, default=5,
                        help="training passes over all 12 areas (default 5 = 60 hits)")
    parser.add_argument("--ask", action="store_true",
                        help="ask before saving instead of comparing the check accuracy")
    parser.add_argument("--status", action="store_true", help="only show the stored calibration")
    parser.add_argument("--factory-reset", action="store_true",
                        help="forget the saved calibration and use the factory model")
    parser.add_argument("--log", type=Path, help="write the per-hit report as JSON")
    args = parser.parse_args()

    with serial.Serial(args.port, 115_200, timeout=0.05, write_timeout=0.5) as port:
        client = CalibrationClient(port)
        if args.status or args.factory_reset:
            client.set_calibration_mode()
            operation = CalibrationOp.FACTORY_RESET if args.factory_reset else CalibrationOp.STATUS
            status = client.calibration(operation)
            print(f"使用中のモデル: {status.source.name.lower()}、"
                  f"保存済み: {'あり' if status.saved_valid else 'なし'}（{status.saved_count}打）")
            return
        confirm = None
        if args.ask:
            confirm = lambda message: input(message + " [y/N] ").strip().lower() == "y"
        report = run_calibration(client, args.rounds, print, confirm)
    if args.log is not None:
        args.log.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")


if __name__ == "__main__":
    main()
