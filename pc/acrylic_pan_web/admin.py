"""Non-destructive exhibit diagnostics and narrowly-scoped system actions."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import threading
import time
from typing import Any

from pc.acrylic_pan_monitor.serial_link import available_ports
from pc.acrylic_pan_web.audio_synth import audio_backend_name, synthesize_note
from pc.acrylic_pan_web.exhibit import video_list


PROCESS_STARTED = time.monotonic()
ACTION_HELPERS = {
    "service_restart": "/usr/local/sbin/acrylic-pan-service-restart",
    "reboot": "/usr/local/sbin/acrylic-pan-reboot",
    "poweroff": "/usr/local/sbin/acrylic-pan-poweroff",
}


def _command(args: list[str], timeout: float = 3.0) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False,
        )
        return result.returncode == 0, (result.stdout or result.stderr).strip()
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)


def _check(identifier: str, label: str, status: str, summary: str,
           *, required: bool = True, details: dict[str, Any] | None = None,
           action: str | None = None) -> dict[str, Any]:
    return {
        "id": identifier, "label": label, "status": status, "summary": summary,
        "required": required, "details": details or {}, "action": action,
    }


def run_diagnostics(controller: Any, video_dir: Path) -> dict[str, Any]:
    """Return one complete, display-oriented health report.

    The UART probe is delegated to the controller and is automatically skipped
    while inference/collection/calibration is active.  No mode, ODL data, or
    saved model is changed by this routine.
    """
    checks: list[dict[str, Any]] = []
    status = controller.status()
    ports = available_ports()

    connected = bool(status["connected"])
    checks.append(_check(
        "usb_serial", "USBシリアル", "pass" if connected else "fail",
        (f"{status.get('port')} を認識" if connected else "Solist-AIに接続できていません"),
        details={"ports": ports, "selected": status.get("port")},
        action=None if connected else "USBケーブルと電源を確認してください",
    ))

    probe = controller.probe_device()
    if probe.get("active_probe") == "completed":
        hello = probe.get("hello", {})
        firmware = probe.get("firmware", {})
        identity_ok = hello.get("identity") == "AcrylicPanCollector"
        checks.append(_check(
            "solist_protocol", "Solist-AI通信", "pass" if probe.get("ok") else "fail",
            (f"HELLO/STATUS応答 {hello.get('rtt_ms', '-')} / {firmware.get('rtt_ms', '-')} ms"
             if probe.get("ok") else "応答内容が正しくありません"),
            details={"hello": hello, "firmware": firmware},
            action=None if identity_ok else "ファームウェアと接続ポートを確認してください",
        ))
        contract_ok = (
            firmware.get("sample_rate_hz") == 25_600
            and firmware.get("sample_count") in (512, 2048)
            and firmware.get("mode") in {"collection", "inference", "instrument", "device_position", "calibration"}
        )
        checks.append(_check(
            "firmware_contract", "ファーム設定", "pass" if contract_ok else "fail",
            (f"{firmware.get('sample_rate_hz')} Hz / {firmware.get('sample_count')} samples / "
             f"{firmware.get('mode')}"), details=firmware,
            action=None if contract_ok else "STATUS設定値が想定外です",
        ))
    elif probe.get("active_probe") == "skipped_busy":
        checks.append(_check(
            "solist_protocol", "Solist-AI通信", "warn",
            "動作中のため能動検査を省略（受信状態は正常）",
            details={"mode": status.get("device_mode")},
            action="演奏・学習を停止して再検査するとHELLO/STATUSを確認できます",
        ))
    else:
        checks.append(_check(
            "solist_protocol", "Solist-AI通信", "fail",
            probe.get("error") or "HELLO/STATUS応答がありません", details=probe,
            action="USBケーブル、電源、ファームウェアを確認してください",
        ))

    baud = int(status.get("baudrate") or 0)
    checks.append(_check(
        "uart_speed", "通信速度", "pass" if baud == 960_000 else ("warn" if connected else "fail"),
        f"{baud:,} bit/s" if baud else "未接続",
        details={"expected": 960_000, "actual": baud},
        action=None if baud == 960_000 else "再接続して高速UART交渉を確認してください",
    ))

    stats = status.get("stats", {})
    assembly = status.get("assembly", {})
    errors = sum(int(stats.get(key, 0)) for key in (
        "decoder_errors", "missing_sequences", "duplicate_sequences",
        "out_of_order_sequences", "save_errors",
    )) + int(assembly.get("conflicts", 0)) + int(assembly.get("timed_out", 0))
    checks.append(_check(
        "protocol_quality", "通信品質", "pass" if connected and errors == 0 else ("warn" if connected else "fail"),
        (f"受信 {stats.get('frames_received', 0)} frame / 異常 {errors}"
         if connected else "通信データなし"),
        details={"stats": stats, "assembly": assembly},
        action=None if errors == 0 else "USBケーブルを挿し直して再検査してください",
    ))

    ai_age = status.get("last_ai_age_s")
    if ai_age is not None and ai_age <= 300:
        ai_state, ai_text, ai_action = "pass", f"{ai_age:.1f}秒前に打撃推論を受信", None
    else:
        ai_state, ai_text = "warn", "最近の打撃データを確認できていません"
        ai_action = "板を1回叩いてから再検査してください"
    checks.append(_check(
        "sensor_path", "KX134入力～60位置推論（実打）", ai_state, ai_text,
        details={"last_ai_age_s": ai_age, "latest": status.get("latest_ai")}, action=ai_action,
    ))

    try:
        wav = synthesize_note({"note": "C4", "instrument": "steel_drum", "length": ".25"})
        backend = audio_backend_name()
        audio_ok = len(wav) > 44 and backend != "procedural-fallback"
        checks.append(_check(
            "audio", "音源生成", "pass" if audio_ok else "warn",
            f"{backend} / {len(wav):,} bytes", details={"backend": backend},
            action=None if audio_ok else "FluidSynthとSoundFontを確認してください",
        ))
    except Exception as error:
        checks.append(_check("audio", "音源生成", "fail", str(error),
                             action="音声ライブラリを再インストールしてください"))

    videos = video_list(video_dir)
    checks.append(_check(
        "videos", "デモ動画", "pass" if videos else "warn",
        f"{len(videos)}本 / {sum(item['size'] for item in videos) / 1_000_000:.1f} MB",
        required=False, details={"videos": videos},
        action=None if videos else "web/assets/pv にMP4を配置してください",
    ))

    disk = shutil.disk_usage(Path.home())
    free_gib = disk.free / (1024 ** 3)
    disk_state = "pass" if free_gib >= 2 else ("warn" if free_gib >= 0.5 else "fail")
    checks.append(_check(
        "storage", "ストレージ", disk_state, f"空き {free_gib:.1f} GiB",
        details={"free_bytes": disk.free, "total_bytes": disk.total},
        action=None if disk_state == "pass" else "不要な収録データを退避してください",
    ))

    service_ok, service_state = _command(["systemctl", "is-active", "acrylic-pan-exhibit"])
    enabled_ok, enabled_state = _command(["systemctl", "is-enabled", "acrylic-pan-exhibit"])
    autostart = Path.home() / ".config/labwc/autostart"
    kiosk_configured = autostart.is_file() and "start-exhibit-kiosk.sh" in autostart.read_text(errors="replace")
    checks.append(_check(
        "autostart", "自動起動", "pass" if service_ok and enabled_ok and kiosk_configured else "fail",
        f"service={service_state or '-'}, enabled={enabled_state or '-'}, kiosk={'yes' if kiosk_configured else 'no'}",
        details={"service_active": service_ok, "service_enabled": enabled_ok,
                 "kiosk_configured": kiosk_configured},
        action=None if service_ok and enabled_ok and kiosk_configured else "展示インストーラーを再実行してください",
    ))

    tailscale_ok, tailscale_text = _command(["tailscale", "status", "--json"])
    tailscale_ip = None
    if tailscale_ok:
        try:
            tailscale_data = json.loads(tailscale_text)
            tailscale_ip = (
                tailscale_data.get("Self", {}).get("TailscaleIPs")
                or tailscale_data.get("TailscaleIPs")
                or [None]
            )[0]
        except (json.JSONDecodeError, AttributeError):
            tailscale_ok = False
    checks.append(_check(
        "tailscale", "Tailscale保守接続", "pass" if tailscale_ok else "warn",
        tailscale_ip or "未接続（展示動作には影響しません）", required=False,
        action=None if tailscale_ok else "ネットワーク接続とtailscale statusを確認してください",
    ))

    codex_ok, codex_text = _command(["codex", "login", "status"])
    checks.append(_check(
        "codex", "Codex保守ツール", "pass" if codex_ok else "warn",
        codex_text or "未ログイン（展示動作には影響しません）", required=False,
        action=None if codex_ok else "必要な場合だけcodex loginを実行してください",
    ))

    camera_names = []
    for path in sorted(Path("/sys/class/video4linux").glob("video*/name")):
        try:
            name = path.read_text(errors="replace").strip()
            if name and not any(word in name.lower() for word in ("bcm2835", "codec", "isp", "hevc")):
                camera_names.append(name)
        except OSError:
            pass
    checks.append(_check(
        "camera", "USBカメラ", "pass" if camera_names else "warn",
        ", ".join(camera_names) if camera_names else "未接続（カメラを使わない展示は可能）",
        required=False, details={"devices": camera_names},
        action=None if camera_names else "使用する場合はUSBカメラを接続してください",
    ))

    required_states = [item["status"] for item in checks if item["required"]]
    overall = "fail" if "fail" in required_states else "warn" if "warn" in required_states else "pass"
    temp_path = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        temperature_c = round(int(temp_path.read_text().strip()) / 1000, 1)
    except (OSError, ValueError):
        temperature_c = None
    return {
        "overall": overall,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "system": {
            "hostname": socket.gethostname(), "platform": platform.platform(),
            "architecture": platform.machine(), "temperature_c": temperature_c,
            "server_uptime_s": round(time.monotonic() - PROCESS_STARTED, 1),
        },
    }


def start_system_action(action: str, on_error: Any) -> None:
    """Start exactly one preinstalled root helper; no user text reaches a shell."""
    helper = ACTION_HELPERS.get(action)
    if helper is None:
        raise ValueError("unsupported system action")
    if not Path(helper).is_file():
        raise OSError(f"system action helper is not installed: {helper}")

    def worker() -> None:
        time.sleep(0.7)  # let the HTTP response reach the browser first
        try:
            result = subprocess.run(
                ["sudo", "-n", helper], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, timeout=15, check=False,
            )
            if result.returncode:
                on_error(result.stderr.strip() or f"system action failed: {result.returncode}")
        except Exception as error:
            on_error(str(error))

    threading.Thread(target=worker, name=f"admin-{action}", daemon=True).start()
