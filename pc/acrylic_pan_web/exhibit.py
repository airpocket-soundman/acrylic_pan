"""Exhibition kiosk support: static pages, demo videos with Range requests, and serial auto-connect."""
from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
import mimetypes
import re
import threading
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from pc.acrylic_pan_monitor.serial_link import available_ports

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

EXHIBIT_DIR = Path(__file__).with_name("static") / "exhibit"
DEFAULT_VIDEO_DIR = Path(__file__).resolve().parents[2] / "web" / "assets" / "pv"
VIDEO_SUFFIXES = {".mp4", ".webm", ".m4v"}
PAGES = {"/exhibit": "play.html", "/exhibit/": "play.html"}
CHUNK = 256 * 1024


def video_list(video_dir: Path) -> list[dict[str, Any]]:
    if not video_dir.is_dir():
        return []
    return [
        {"name": path.name, "title": path.stem.replace("_", " "), "size": path.stat().st_size,
         "url": "/exhibit-media/" + path.name}
        for path in sorted(video_dir.iterdir())
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
    ]


def serve_exhibit_static(handler: "BaseHTTPRequestHandler", path: str) -> bool:
    """Serve /exhibit/<file> from the exhibit static folder.  Returns False when the path is not ours."""
    if path not in PAGES and not path.startswith("/exhibit/"):
        return False
    relative = PAGES.get(path) or path[len("/exhibit/"):]
    target = (EXHIBIT_DIR / relative).resolve()
    if EXHIBIT_DIR.resolve() not in target.parents or not target.is_file():
        _send(handler, HTTPStatus.NOT_FOUND, b'{"error":"not found"}', "application/json")
        return True
    types = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
             ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
             ".jpg": "image/jpeg"}
    _send(handler, HTTPStatus.OK, target.read_bytes(),
          types.get(target.suffix.lower(), "application/octet-stream"))
    return True


def _send(handler: "BaseHTTPRequestHandler", status: HTTPStatus, body: bytes, content_type: str) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def serve_video(handler: "BaseHTTPRequestHandler", path: str, video_dir: Path) -> bool:
    """Serve /exhibit-media/<name> with HTTP Range support so the player can seek."""
    prefix = "/exhibit-media/"
    if not path.startswith(prefix):
        return False
    name = unquote(path[len(prefix):])
    target = (video_dir / name).resolve()
    if (Path(name).name != name or target.suffix.lower() not in VIDEO_SUFFIXES
            or not target.is_file() or video_dir.resolve() not in target.parents):
        _send(handler, HTTPStatus.NOT_FOUND, b'{"error":"not found"}', "application/json")
        return True
    size = target.stat().st_size
    start, end, status = 0, size - 1, HTTPStatus.OK
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", handler.headers.get("Range", ""))
    if match and (match.group(1) or match.group(2)):
        if match.group(1):
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else size - 1
        else:
            start = max(0, size - int(match.group(2)))
        end = min(end, size - 1)
        if start > end:
            handler.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.end_headers()
            return True
        status = HTTPStatus.PARTIAL_CONTENT
    handler.send_response(status)
    handler.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "video/mp4")
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(end - start + 1))
    if status == HTTPStatus.PARTIAL_CONTENT:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.end_headers()
    try:
        with target.open("rb") as file:
            file.seek(start)
            left = end - start + 1
            while left > 0:
                block = file.read(min(CHUNK, left))
                if not block:
                    break
                handler.wfile.write(block)
                left -= len(block)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        pass   # the player closed the connection while seeking
    return True


class AutoConnector(threading.Thread):
    """Keeps the controller connected to the board: tries the given port (or every port) until it answers."""

    def __init__(self, controller: Any, port: str = "auto", interval: float = 3.0) -> None:
        super().__init__(daemon=True, name="exhibit-auto-connect")
        self.controller, self.port, self.interval = controller, port, interval
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                if not self.controller.link.connected:
                    self._try_connect()
            except Exception as error:  # keep trying: the board may be plugged in later
                print(f"auto-connect: {error}")
            self._stop_event.wait(self.interval)

    def _try_connect(self) -> None:
        ports = available_ports() if self.port == "auto" else [self.port]
        for port in ports:
            try:
                # Opening a tty is not enough: another USB serial device may be
                # listed first.  Only claim the port after the firmware answers.
                self.controller.connect(port, require_response=True)
                self.controller.set_panel_profile("400x300x5")
                print(f"auto-connect: connected to {port}")
                return
            except Exception:
                try:
                    self.controller.disconnect()
                except Exception:
                    pass
