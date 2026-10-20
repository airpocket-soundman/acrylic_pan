"""Does the on-board ODL absorb a change of mallet?  A guided, recorded session on the board.

Phases (the mallet is changed before the session, e.g. to a felt-tip pen):
  1. check    : ``--check-rounds`` rounds over the 12 areas, nothing is learned (factory beta)
  2. calibrate: ``--train-rounds`` rounds, every hit is learned by the AxlCORE ODL
  3. check    : ``--check-rounds`` rounds again with the calibrated beta, nothing is learned

The accuracy of phase 3 against phase 1 says how much of the mallet difference the ODL absorbed.  The working
calibration is discarded at the end unless ``--commit`` is given.

    python -m sim.odl_mallet_experiment --port COM3
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

CLASS_COUNT = 12
COLUMNS = 4


def wilson(correct: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval of a proportion."""
    if total == 0:
        return 0.0, 0.0
    p = correct / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return centre - margin, centre + margin


def summarise(hits: list[dict]) -> dict:
    result = {}
    for phase in ("check_before", "calibrate", "check_after"):
        members = [h for h in hits if h["phase"] == phase]
        if not members:
            continue
        correct = sum(h["predicted"] == h["area"] for h in members)
        low, high = wilson(correct, len(members))
        rounds = sorted({h["round"] for h in members})
        per_round = [sum(h["predicted"] == h["area"] for h in members if h["round"] == r) / CLASS_COUNT
                     for r in rounds]
        per_area = [sum(h["predicted"] == h["area"] for h in members if h["area"] == a) /
                    max(1, sum(h["area"] == a for h in members)) for a in range(CLASS_COUNT)]
        confusion: dict[str, int] = {}
        for h in members:
            if h["predicted"] != h["area"]:
                key = f"{h['area'] + 1}->{h['predicted'] + 1}"
                confusion[key] = confusion.get(key, 0) + 1
        result[phase] = {"hits": len(members), "correct": correct, "accuracy": correct / len(members),
                         "wilson95": [low, high], "per_round": per_round, "per_area": per_area,
                         "top_confusions": sorted(confusion.items(), key=lambda kv: -kv[1])[:6]}
    return result


def run(port_name: str, check_rounds: int, train_rounds: int, commit: bool, log: Path, say=print) -> dict:
    import serial
    from pc.acrylic_pan_monitor.calibrate import CalibrationClient, area_label
    from pc.acrylic_pan_monitor.protocol import CalibrationOp

    hits: list[dict] = []
    log.parent.mkdir(parents=True, exist_ok=True)

    def save(extra: dict | None = None) -> None:
        payload = {"check_rounds": check_rounds, "train_rounds": train_rounds, "hits": hits}
        if extra:
            payload.update(extra)
        log.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

    with serial.Serial(port_name, 115_200, timeout=0.05, write_timeout=0.5) as port:
        client = CalibrationClient(port)
        client.set_calibration_mode()
        client.calibration(CalibrationOp.BEGIN)          # factory beta in the AxlCORE, P0 as the prior
        plan = ([("check_before", r, False) for r in range(1, check_rounds + 1)] +
                [("calibrate", r, True) for r in range(1, train_rounds + 1)] +
                [("check_after", r, False) for r in range(1, check_rounds + 1)])
        names = {"check_before": "確認(学習なし)", "calibrate": "ODL(学習する)", "check_after": "確認(学習なし・学習後)"}
        started = time.time()
        for phase, round_index, train in plan:
            total = check_rounds if phase != "calibrate" else train_rounds
            say(f"\n=== {names[phase]}  {round_index}/{total} 周目 ===")
            for area in range(CLASS_COUNT):
                say(f"  {area_label(area)} を叩いてください")
                result = client.hit(area if train else None)
                hits.append({"phase": phase, "round": round_index, "area": area, "train": train,
                             "predicted": int(result.predicted_class), "trained": bool(result.trained),
                             "train_us": int(result.train_us), "outputs": [float(v) for v in result.outputs],
                             "elapsed_s": round(time.time() - started, 1)})
                mark = "○" if result.predicted_class == area else "×"
                say(f"    {mark} 認識: {area_label(result.predicted_class)}")
                save()
        status = client.calibration(CalibrationOp.COMMIT if commit else CalibrationOp.DISCARD)
        say(f"\n作業用キャリブレーションを{'保存' if commit else '破棄'}しました(使用中のβ: {status.source.name})")

    summary = summarise(hits)
    save({"summary": summary, "committed": commit})
    say("\n=== 結果 ===")
    for phase, entry in summary.items():
        low, high = entry["wilson95"]
        say(f"{names[phase]}: {entry['correct']}/{entry['hits']} = {entry['accuracy']:.1%}  (95%区間 {low:.1%}〜{high:.1%})")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True)
    parser.add_argument("--check-rounds", type=int, default=10)
    parser.add_argument("--train-rounds", type=int, default=5)
    parser.add_argument("--commit", action="store_true", help="keep the calibrated beta in the FRAM")
    parser.add_argument("--log", type=Path,
                        default=Path("artifacts/odl_mallet_experiment") / time.strftime("session_%Y%m%d_%H%M%S.json"))
    args = parser.parse_args()
    run(args.port, args.check_rounds, args.train_rounds, args.commit, args.log)


if __name__ == "__main__":
    main()
