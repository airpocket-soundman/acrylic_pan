"""Dependency-free local HTTP server for serial capture and visualization."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import queue
import threading
import webbrowser
from typing import Any
from urllib.parse import urlparse

from pc.acrylic_pan_monitor.ai_validation import (
    DEFAULT_GOLDEN_PATH,
    compare_ai_result,
    load_golden_case,
)
from pc.acrylic_pan_monitor.protocol import (
    EventData,
    Frame,
    MessageType,
    decode_ai_result,
    decode_event,
    encode_frame,
)
from pc.acrylic_pan_monitor.recorder import Recorder, ReceiveStats, make_demo_event
from pc.acrylic_pan_monitor.serial_link import SerialLink, available_ports
from pc.acrylic_pan_monitor.signal_processing import prepare_plot_data


STATIC_DIR = Path(__file__).with_name("static")


@dataclass
class CollectionState:
    """Progress for fixed-order, eight-area guided acquisition."""

    active: bool = False
    finished: bool = False
    repetitions: int = 0
    completed_samples: int = 0
    per_class_counts: list[int] = field(default_factory=lambda: [0] * 8)
    order: tuple[int, ...] = tuple(range(8))

    @property
    def total_samples(self) -> int:
        return len(self.order) * self.repetitions

    @property
    def current_class_id(self) -> int | None:
        if not self.active or self.completed_samples >= self.total_samples:
            return None
        return self.order[self.completed_samples // self.repetitions]

    def as_dict(self) -> dict[str, Any]:
        current = self.current_class_id
        return {
            "active": self.active,
            "finished": self.finished,
            "repetitions": self.repetitions,
            "completed_samples": self.completed_samples,
            "total_samples": self.total_samples,
            "current_class_id": current,
            "current_repetition": self.per_class_counts[current] + 1 if current is not None else None,
            "per_class_counts": list(self.per_class_counts),
            "order": list(self.order),
        }


class AcquisitionController:
    """Thread-safe bridge between serial I/O, Recorder, and the HTTP layer."""

    def __init__(
        self,
        output_root: str | Path = "data/raw/sessions",
        golden_path: str | Path = DEFAULT_GOLDEN_PATH,
    ) -> None:
        self.output_root = Path(output_root).resolve()
        self.port: str | None = None
        self.baudrate = 115_200
        self.auto_save = True
        self.class_id: int | None = None
        self.stats = ReceiveStats()
        self.recorder: Recorder | None = None
        self.latest: dict[str, Any] | None = None
        self.latest_ai: dict[str, Any] | None = None
        self.golden_path = Path(golden_path).resolve()
        self.collection = CollectionState()
        self.identity: str | None = None
        self.last_error: str | None = None
        self.last_control: dict[str, Any] | None = None
        self._command_sequence = 1
        self._queue: queue.Queue[Frame | Exception] = queue.Queue()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.link = SerialLink(self._queue.put, self._queue.put)
        self._worker = threading.Thread(target=self._consume, name="apan-web-consumer", daemon=True)
        self._worker.start()

    def connect(self, port: str, baudrate: int = 115_200) -> None:
        self.link.connect(port, baudrate)
        with self._lock:
            self.port, self.baudrate, self.last_error = port, baudrate, None

    def disconnect(self) -> None:
        self.link.disconnect()
        with self._lock:
            if self.collection.active:
                self.collection.active = False
                self.collection.finished = False

    def send_command(self, command: str) -> dict[str, Any]:
        """Invoke a collector function through the framed UART API."""
        kinds = {
            "ping": MessageType.HELLO,
            "status": MessageType.STATUS,
            "capture": MessageType.CAPTURE,
            "start": MessageType.START,
            "stop": MessageType.STOP,
        }
        try:
            kind = kinds[command.lower()]
        except KeyError as error:
            raise ValueError(f"unsupported command: {command}") from error
        if not self.link.connected:
            raise OSError("serial port is not connected")
        with self._lock:
            sequence = self._command_sequence
            self._command_sequence += 1
            self.last_control = {"command": command.lower(), "sequence": sequence, "state": "sent"}
        self.link.send(encode_frame(Frame(kind, sequence)))
        return dict(self.last_control)

    def send_ai_selftest(self, case_id: int) -> dict[str, Any]:
        if not 0 <= case_id <= 255:
            raise ValueError("case_id must be between 0 and 255")
        if not self.link.connected:
            raise OSError("serial port is not connected")
        with self._lock:
            sequence = self._command_sequence
            self._command_sequence += 1
            self.last_control = {
                "command": "ai_selftest",
                "case_id": case_id,
                "sequence": sequence,
                "state": "sent",
            }
        self.link.send(encode_frame(Frame(MessageType.AI_SELFTEST, sequence, bytes([case_id]))))
        return dict(self.last_control)

    def start_collection(
        self, repetitions: int, output_root: str | Path | None = None
    ) -> dict[str, Any]:
        if not 1 <= repetitions <= 1000:
            raise ValueError("repetitions must be between 1 and 1000")
        if not self.link.connected:
            raise OSError("serial port is not connected")
        with self._lock:
            if self.collection.active:
                raise ValueError("collection is already active")
            self.new_session(output_root, class_id=None, metadata={
                "mode": "guided_8area_collection",
                "collection_plan": {
                    "area_count": 8,
                    "repetitions": repetitions,
                    "order": list(range(8)),
                    "total_samples": 8 * repetitions,
                },
            })
            self.collection = CollectionState(active=True, repetitions=repetitions)
        try:
            self.send_command("start")
        except Exception:
            with self._lock:
                self.collection.active = False
            raise
        return self.collection_status()

    def stop_collection(self) -> dict[str, Any]:
        with self._lock:
            self.collection.active = False
            self.collection.finished = False
        if self.link.connected:
            self.send_command("stop")
        return self.collection_status()

    def collection_status(self) -> dict[str, Any]:
        with self._lock:
            return self.collection.as_dict()

    def new_session(
        self,
        output_root: str | Path | None = None,
        class_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        with self._lock:
            if class_id is not None and not 0 <= class_id < 8:
                raise ValueError("class_id must be between 0 and 7")
            if self.recorder is not None:
                self.recorder.close()
            if output_root is not None:
                self.output_root = Path(output_root).expanduser().resolve()
            self.class_id = class_id
            self.recorder = Recorder(self.output_root)
            session_metadata: dict[str, Any] = {
                "application": "acrylic_pan_web",
                "serial_port": self.port,
                "baudrate": self.baudrate,
                "device_identity": self.identity,
            }
            session_metadata.update(metadata or {})
            return self.recorder.begin_session(session_metadata)

    def demo(self) -> dict[str, Any]:
        with self._lock:
            sequence = (self.latest or {}).get("sequence", 0) + 1
        event = make_demo_event(sequence)
        return self._process_event(event, source="demo", count_as_received=False)

    def status(self) -> dict[str, Any]:
        with self._lock:
            self.stats.decoder_errors = self.link.decoder_error_count
            return {
                "connected": self.link.connected,
                "port": self.port,
                "baudrate": self.baudrate,
                "identity": self.identity,
                "last_error": self.last_error,
                "last_control": self.last_control,
                "latest_ai": self.latest_ai,
                "golden_path": str(self.golden_path),
                "collection": self.collection.as_dict(),
                "auto_save": self.auto_save,
                "class_id": self.class_id,
                "output_root": str(self.output_root),
                "session_dir": str(self.recorder.session_dir) if self.recorder and self.recorder.session_dir else None,
                "stats": {
                    "frames_received": self.stats.frames_received,
                    "events_received": self.stats.events_received,
                    "decoder_errors": self.stats.decoder_errors,
                    "missing_sequences": self.stats.missing_sequences,
                    "duplicate_sequences": self.stats.duplicate_sequences,
                    "out_of_order_sequences": self.stats.out_of_order_sequences,
                    "events_saved": self.stats.events_saved,
                    "save_errors": self.stats.save_errors,
                },
            }

    def close(self) -> None:
        self._stop.set()
        self.link.disconnect()
        self._worker.join(timeout=1)
        with self._lock:
            if self.recorder is not None:
                self.recorder.close()

    def _ensure_session(self) -> None:
        if self.recorder is None or not self.recorder.active:
            self.new_session(class_id=self.class_id)

    def _process_event(self, event: EventData, source: str, count_as_received: bool) -> dict[str, Any]:
        plot = prepare_plot_data(event)
        payload = {
            "sequence": event.sequence,
            "sample_rate_hz": event.sample_rate_hz,
            "trigger_index": event.trigger_index,
            "trigger_time_ms": plot.trigger_time_ms,
            "peak_abs": event.peak_abs,
            "flags": event.flags,
            "timestamp_us": event.timestamp_us,
            "time_ms": plot.time_ms.tolist(),
            "samples": plot.samples.tolist(),
            "frequency_hz": plot.frequency_hz.tolist(),
            "magnitude_db": plot.magnitude_db.tolist(),
            "source": source,
        }
        rearm = False
        with self._lock:
            if count_as_received:
                self.stats.observe_event(event.sequence)
            self.latest = payload
            if self.auto_save:
                try:
                    self._ensure_session()
                    assert self.recorder is not None
                    collection_class = (
                        self.collection.current_class_id
                        if self.collection.active and source == "serial"
                        else None
                    )
                    label = collection_class if collection_class is not None else self.class_id
                    annotations: dict[str, Any] = {"source": source}
                    if collection_class is not None:
                        annotations.update({
                            "collection": True,
                            "collection_index": self.collection.completed_samples,
                            "target_class_id": collection_class,
                            "target_area": collection_class + 1,
                            "repetition": self.collection.per_class_counts[collection_class] + 1,
                        })
                    self.recorder.record_event(event, class_id=label, annotations=annotations)
                    self.stats.events_saved += 1
                    if collection_class is not None:
                        self.collection.per_class_counts[collection_class] += 1
                        self.collection.completed_samples += 1
                        if self.collection.completed_samples >= self.collection.total_samples:
                            self.collection.active = False
                            self.collection.finished = True
                            self.recorder.close()
                            self.last_control = {"response": "collection_complete"}
                        else:
                            rearm = True
                except Exception as error:
                    self.stats.save_errors += 1
                    self.last_error = f"保存エラー: {error}"
        if rearm:
            try:
                self.send_command("start")
            except Exception as error:
                with self._lock:
                    self.collection.active = False
                    self.last_error = f"再アームエラー: {error}"
        return payload

    def _consume(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if isinstance(item, Exception):
                with self._lock:
                    self.last_error = f"通信エラー: {item}"
                    self.collection.active = False
                    self.collection.finished = False
                continue
            with self._lock:
                self.stats.observe_frame()
            if item.message_type == MessageType.EVENT_DATA:
                try:
                    self._process_event(decode_event(item), "serial", True)
                except Exception as error:
                    with self._lock:
                        self.last_error = f"イベント解析エラー: {error}"
            elif item.message_type == MessageType.HELLO:
                with self._lock:
                    self.identity = item.payload.decode("ascii", errors="replace")
                    self.last_control = {"response": "hello", "payload": self.identity, "sequence": item.sequence}
            elif item.message_type == MessageType.AI_RESULT:
                try:
                    result = decode_ai_result(item)
                    payload: dict[str, Any] = {
                        "case_id": result.case_id,
                        "predicted_class": result.predicted_class,
                        "outputs": list(result.outputs),
                        "sequence": result.sequence,
                        "timestamp_us": result.timestamp_us,
                        "comparison": {"available": False},
                    }
                    if self.golden_path.is_file():
                        golden = load_golden_case(self.golden_path, result.case_id)
                        if golden is not None:
                            payload["comparison"] = compare_ai_result(result, golden)
                    with self._lock:
                        self.latest_ai = payload
                        self.last_control = {
                            "response": "ai_result",
                            "case_id": result.case_id,
                            "sequence": result.sequence,
                            "passed": payload["comparison"].get("passed"),
                        }
                except Exception as error:
                    with self._lock:
                        self.last_error = f"AI result error: {error}"
            elif item.message_type in (MessageType.STATUS, MessageType.ACK, MessageType.NACK):
                with self._lock:
                    self.last_control = {
                        "response": item.message_type.name.lower(),
                        "payload_hex": item.payload.hex(),
                        "sequence": item.sequence,
                    }


class ApiHandler(BaseHTTPRequestHandler):
    controller: AcquisitionController

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/status":
            return self._json(self.controller.status())
        if path == "/api/ports":
            return self._json({"ports": available_ports()})
        if path == "/api/events/latest":
            return self._json(self.controller.latest or {}, HTTPStatus.OK if self.controller.latest else HTTPStatus.NO_CONTENT)
        if path == "/api/ai/latest":
            return self._json(self.controller.latest_ai or {}, HTTPStatus.OK if self.controller.latest_ai else HTTPStatus.NO_CONTENT)
        if path == "/api/session":
            return self._json(self.controller.status())
        if path == "/api/collection":
            return self._json(self.controller.collection_status())
        self._static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self._request_json()
            if path == "/api/connect":
                self.controller.connect(str(body["port"]), int(body.get("baudrate", 115_200)))
                return self._json(self.controller.status())
            if path == "/api/disconnect":
                self.controller.disconnect()
                return self._json(self.controller.status())
            if path == "/api/demo":
                return self._json(self.controller.demo())
            if path == "/api/command":
                return self._json(self.controller.send_command(str(body["command"])))
            if path == "/api/ai/selftest":
                return self._json(self.controller.send_ai_selftest(int(body.get("case_id", 0))))
            if path == "/api/collection/start":
                return self._json(self.controller.start_collection(
                    int(body.get("repetitions", 10)), body.get("output_root")
                ))
            if path == "/api/collection/stop":
                return self._json(self.controller.stop_collection())
            if path == "/api/session":
                session = self.controller.new_session(body.get("output_root"), body.get("class_id"))
                return self._json({"session_dir": str(session)})
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (KeyError, TypeError, ValueError, OSError) as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self._json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _request_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 65_536:
            raise ValueError("request body is too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: str) -> None:
        names = {
            "/": "index.html",
            "/index.html": "index.html",
            "/collector.html": "collector.html",
            "/app.js": "app.js",
            "/style.css": "style.css",
        }
        name = names.get(path)
        if name is None:
            return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        data = (STATIC_DIR / name).read_bytes()
        content_type = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}[Path(name).suffix]
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


def create_server(host: str, port: int, controller: AcquisitionController) -> ThreadingHTTPServer:
    handler = type("BoundApiHandler", (ApiHandler,), {"controller": controller})
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Acrylic Pan local acquisition web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output", default="data/raw/sessions")
    parser.add_argument("--page", choices=("index.html", "collector.html"), default="index.html")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    controller = AcquisitionController(args.output)
    server = create_server(args.host, args.port, controller)
    suffix = "" if args.page == "index.html" else args.page
    url = f"http://{args.host}:{server.server_port}/{suffix}"
    print(f"Acrylic Pan monitor: {url}")
    if not args.no_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        controller.close()


if __name__ == "__main__":
    main()
