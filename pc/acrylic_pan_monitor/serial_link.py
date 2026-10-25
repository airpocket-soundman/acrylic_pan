"""Background serial transport for the monitor GUI."""

from __future__ import annotations

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
        self._write_lock = threading.Lock()

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

    def set_baudrate(self, baudrate: int) -> None:
        """Change the host side speed of the open port (the firmware is switched separately)."""
        if self._serial is None:
            raise OSError("serial port is not connected")
        self._serial.baudrate = baudrate
        self._serial.reset_input_buffer()
        self._decoder = FrameStreamDecoder()

    def send(self, packet: bytes) -> None:
        """Write at once from the caller's thread; the reader never delays a request."""
        serial_port = self._serial
        if serial_port is None or not serial_port.is_open:
            raise OSError("serial port is not connected")
        with self._write_lock:
            serial_port.write(packet)

    def _run(self) -> None:
        serial_port = self._serial
        try:
            assert serial_port is not None
            while not self._stop.is_set():
                # Block for the first byte only: read(4096) would hold a short frame
                # until the 50 ms timeout, which is the largest delay on the path.
                data = serial_port.read(1)
                if data:
                    waiting = serial_port.in_waiting
                    if waiting:
                        data += serial_port.read(waiting)
                for frame in self._decoder.feed(data):
                    self.on_frame(frame)
        except Exception as error:  # delivered to the GUI thread through a queue
            if not self._stop.is_set():
                self.on_error(error)
        finally:
            # pyserial can leave ``is_open`` true after a USB unplug even though
            # every read now fails.  Publish the disconnection so AutoConnector
            # can reopen the board when it appears again.
            try:
                serial_port.close()
            except Exception:
                pass
            if self._serial is serial_port:
                self._serial = None
