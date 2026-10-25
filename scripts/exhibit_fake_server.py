"""Exhibit pages without the board: a web server whose serial link is a fake Solist-AI board.

The fake accepts the calibration protocol, answers every strike request after a short delay (as if the visitor hit the
panel) and keeps the saved/working state like the firmware, so /exhibit/odl.html can be tried on a PC.

    python scripts/exhibit_fake_server.py [--port 8765] [--delay 1.2]
"""
from __future__ import annotations

import argparse
import tempfile
import threading
from pathlib import Path
import sys
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pc.acrylic_pan_monitor.protocol import (  # noqa: E402
    CALIBRATION_ACK_PAYLOAD, CALIBRATION_RESULT_PAYLOAD, CalibrationOp, Frame, MessageType, decode_frame,
)
from pc.acrylic_pan_web.server import AcquisitionController, create_server  # noqa: E402

CENTRES = (6, 21, 36, 51, 7, 22, 37, 52, 8, 23, 38, 53)
CONTINUE = 0x10


class FakeBoard:
    def __init__(self, controller: AcquisitionController, delay: float) -> None:
        self.controller, self.delay = controller, delay
        self.source = self.saved_valid = self.saved_count = self.work_count = 0
        self.saved_kind = self.kind = 0xFF
        self.strikes = 0

    def send(self, packet: bytes) -> None:
        frame = decode_frame(packet)
        queue = self.controller._queue
        if frame.message_type != MessageType.CALIBRATION:
            queue.put(Frame(MessageType.ACK, frame.sequence, frame.payload))
            return
        operation, argument = frame.payload[0], frame.payload[1]
        if operation == CalibrationOp.BEGIN:
            kind = argument & ~CONTINUE
            keep = bool(argument & CONTINUE)
            if keep and not (self.saved_valid and self.saved_kind == kind):
                queue.put(Frame(MessageType.NACK, frame.sequence, b""))
                return
            self.source, self.kind = 2, kind
            self.work_count = self.saved_count if keep else 0
        elif operation == CalibrationOp.COMMIT:
            self.source, self.saved_valid, self.saved_count, self.saved_kind = 1, 1, self.work_count, self.kind
        elif operation == CalibrationOp.DISCARD:
            self.source = 1 if self.saved_valid else 0
        elif operation == CalibrationOp.FACTORY_RESET:
            self.source = self.saved_valid = self.saved_count = 0
            self.saved_kind = 0xFF
        queue.put(Frame(MessageType.ACK, frame.sequence, CALIBRATION_ACK_PAYLOAD.pack(
            MessageType.CALIBRATION, operation, self.source, self.saved_valid, self.saved_count, self.work_count,
            self.saved_kind if self.saved_valid else 0xFF, self.kind if self.source == 2 else 0xFF)))
        if operation == CalibrationOp.ARM and argument != 0xFF:
            threading.Timer(self.delay, self._strike, (argument,)).start()

    def _strike(self, target: int) -> None:
        self.strikes += 1
        self.work_count += 1
        position = CENTRES[target] if self.kind == 0 else target
        predicted = position if self.strikes % 5 else (position + 1) % 60   # now and then a wrong guess
        outputs = [0.0] * 60
        outputs[predicted] = 1.0
        self.controller._queue.put(Frame(MessageType.CALIBRATION_RESULT, 99, CALIBRATION_RESULT_PAYLOAD.pack(
            target, predicted, 1, self.source, self.work_count, 0, 1500, 40000, *outputs)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--delay", type=float, default=1.2, help="seconds between a strike request and the result")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as output:
        controller = AcquisitionController(output)
        controller.link.send = FakeBoard(controller, args.delay).send
        with patch.object(type(controller.link), "connected", new_callable=PropertyMock, return_value=True):
            server = create_server("127.0.0.1", args.port, controller)
            print(f"fake board exhibit: http://127.0.0.1:{server.server_port}/exhibit/play.html")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
                controller.close()


if __name__ == "__main__":
    main()
