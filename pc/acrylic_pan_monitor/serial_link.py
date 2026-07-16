"""Background serial transport for the monitor GUI."""

from __future__ import annotations

import queue
import threading
from typing import Callable

import serial
from serial.tools import list_ports

from .protocol import Frame, FrameStreamDecoder


def available_ports() -> list[str]:
    ports = list(list_ports.comports())
    ports.sort(key=lambda port: (not bool(port.vid), port.device))
    return [port.device for port in ports]


class SerialLink:
    def __init__(
        self,
        on_frame: Callable[[Frame], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        self.on_frame = on_frame
        self.on_error = on_error
        self._serial: serial.Serial | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._decoder = FrameStreamDecoder()
        self._outgoing: queue.Queue[bytes] = queue.Queue()

    @property
    def connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @property
    def decoder_error_count(self) -> int:
        """Number of malformed/CRC-failed packets seen on this connection."""
        return self._decoder.error_count

    def connect(self, port: str, baudrate: int = 115200) -> None:
        self.disconnect()
        self._decoder = FrameStreamDecoder()
        self._serial = serial.Serial(port, baudrate, timeout=0.05, write_timeout=0.5)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="apan-serial", daemon=True)
        self._thread.start()

    def disconnect(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        self._thread = None
        if self._serial is not None:
            self._serial.close()
        self._serial = None

    def send(self, packet: bytes) -> None:
        self._outgoing.put(packet)

    def _run(self) -> None:
        try:
            assert self._serial is not None
            while not self._stop.is_set():
                data = self._serial.read(4096)
                for frame in self._decoder.feed(data):
                    self.on_frame(frame)
                try:
                    packet = self._outgoing.get_nowait()
                except queue.Empty:
                    continue
                self._serial.write(packet)
        except Exception as error:  # delivered to the GUI thread through a queue
            if not self._stop.is_set():
                self.on_error(error)
