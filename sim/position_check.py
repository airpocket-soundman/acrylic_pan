"""Guided accuracy check of the on-board 60-class position model with real strikes.

The tool tells you where to strike, waits for the board's result (through the running web server, mode
``device_position``) and compares it with the target.  Targets are the 12 area centres (``--reps`` times each)
and, with ``--corners``, the 48 corner points (25 mm from the centre in both directions).

    python -m sim.position_check --reps 2 --corners
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.request
from pathlib import Path

COLUMNS, ROWS, AREA_MM = 4, 3, 100.0


def area_of(x: float, y: float) -> int:
    return min(ROWS - 1, int(y // AREA_MM)) * COLUMNS + min(COLUMNS - 1, int(x // AREA_MM)) + 1


def label(area: int) -> str:
    return f"エリア{area}({(area - 1) // COLUMNS + 1}行{(area - 1) % COLUMNS + 1}列)"


class Server:
    def __init__(self, base: str) -> None:
        self.base = base

    def call(self, path: str, body: dict | None = None, timeout: float = 15.0):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else None


def targets(reps: int, corners: bool) -> list[dict]:
    rows = []
    for rep in range(reps):
        for area in range(1, COLUMNS * ROWS + 1):
            cx = ((area - 1) % COLUMNS) * AREA_MM + AREA_MM / 2
            cy = ((area - 1) // COLUMNS) * AREA_MM + AREA_MM / 2
            rows.append({"kind": "center", "x": cx, "y": cy, "text": f"{label(area)}の中央"})
    if corners:
        for area in range(1, COLUMNS * ROWS + 1):
            cx = ((area - 1) % COLUMNS) * AREA_MM + AREA_MM / 2
            cy = ((area - 1) // COLUMNS) * AREA_MM + AREA_MM / 2
            for dy, vertical in ((-25, "上"), (25, "下")):
                for dx, side in ((-25, "左"), (25, "右")):
                    rows.append({"kind": "corner", "x": cx + dx, "y": cy + dy,
                                 "text": f"{label(area)}の{vertical}{side}(中心から縦横に25 mm)"})
    return rows


def run(base: str, reps: int, corners: bool, wait_s: float, log: Path, say=print) -> dict:
    server = Server(base)
    status = server.call("/api/status")
    if not status.get("connected"):
        raise SystemExit("ボードに接続されていません。ブラウザの「接続」を押してください。")
    if status.get("inference_active"):
        server.call("/api/inference/stop", {})
    server.call("/api/device/mode", {"mode": "device_position"})
    server.call("/api/inference/retrigger", {"milliseconds": 80})
    latest = (server.call("/api/status").get("latest_ai") or {}).get("sequence")
    server.call("/api/inference/start", {"mode": "device_position"})
    plan = targets(reps, corners)
    say(f"\n推論を開始しました(60クラスのデバイス推論)。画面の指示の位置を、1回ずつ叩いてください。全{len(plan)}打。\n")
    records = []
    after = latest
    for index, target in enumerate(plan, 1):
        say(f"[{index}/{len(plan)}] {target['text']} を叩いてください")
        deadline = time.time() + wait_s
        result = None
        while time.time() < deadline:
            try:
                res = server.call(f"/api/ai/wait?after={'' if after is None else after}&timeout=1.0", timeout=5)
            except Exception:
                continue
            if res and res.get("sequence") is not None and res["sequence"] != after:
                after = res["sequence"]
                result = res
                break
        if result is None:
            say("    × 結果が来ませんでした(検出されなかった)")
            records.append({**target, "missed": True})
            continue
        position = result["position"]
        support = position["probability_map"]["support_xy_mm"]
        top = support[result["predicted_position"]]
        areas = position.get("class_probabilities") or []
        page_area = (areas.index(max(areas)) + 1) if areas else None
        expected = (position["expected_x_mm"], position["expected_y_mm"])
        record = {**target, "missed": False, "top1_x": top[0], "top1_y": top[1],
                  "expected_x": expected[0], "expected_y": expected[1],
                  "top1_distance": math.dist(top, (target["x"], target["y"])),
                  "expected_distance": math.dist(expected, (target["x"], target["y"])),
                  "area_ok": area_of(top[0], top[1]) == area_of(target["x"], target["y"]),
                  "page_area_ok": page_area == area_of(target["x"], target["y"]),
                  "top1_ok": math.dist(top, (target["x"], target["y"])) < 1.0,
                  "confidence": position.get("classification_confidence")}
        records.append(record)
        say(f"    認識: ({top[0]:.0f}, {top[1]:.0f}) {label(area_of(top[0], top[1]))}  "
            f"{'○' if record['top1_ok'] else ('△エリアは一致' if record['area_ok'] else '×')}  "
            f"確率 {record['confidence']:.2f}")
    server.call("/api/inference/stop", {})

    def rate(rows, key):
        return sum(r[key] for r in rows) / len(rows) if rows else float("nan")
    hit = [r for r in records if not r["missed"]]
    summary = {"targets": len(plan), "missed": len(plan) - len(hit)}
    for name, rows in (("center", [r for r in hit if r["kind"] == "center"]),
                       ("corner", [r for r in hit if r["kind"] == "corner"])):
        if rows:
            summary[name] = {"hits": len(rows), "top1_accuracy": rate(rows, "top1_ok"), "area_accuracy": rate(rows, "area_ok"),
                             "page_area_accuracy": rate(rows, "page_area_ok"),
                             "mean_top1_distance_mm": sum(r["top1_distance"] for r in rows) / len(rows),
                             "mean_expected_distance_mm": sum(r["expected_distance"] for r in rows) / len(rows)}
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"summary": summary, "records": records}, ensure_ascii=False) + "\n", encoding="utf-8")
    say("\n=== 結果 ===")
    say(f"指示 {summary['targets']}打、検出されなかった {summary['missed']}打(一発目: {'検出なし' if records and records[0].get('missed') else '検出あり'})")
    for name, title in (("center", "エリア中央"), ("corner", "四隅")):
        if name in summary:
            s = summary[name]
            say(f"{title}: 60位置の正解 {s['top1_accuracy']:.1%}、エリア正解 {s['area_accuracy']:.1%}、デモが鳴らすエリアの正解 {s['page_area_accuracy']:.1%}、"
                f"最尤点の平均距離 {s['mean_top1_distance_mm']:.1f} mm、期待座標の平均距離 {s['mean_expected_distance_mm']:.1f} mm({s['hits']}打)")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--reps", type=int, default=2, help="strikes per area centre")
    parser.add_argument("--corners", action="store_true", help="also check the 48 corner points")
    parser.add_argument("--wait", type=float, default=25.0, help="seconds to wait for each strike")
    parser.add_argument("--log", type=Path, default=Path("artifacts/position_check") / time.strftime("check_%Y%m%d_%H%M%S.json"))
    args = parser.parse_args()
    run(args.base, args.reps, args.corners, args.wait, args.log)


if __name__ == "__main__":
    main()
