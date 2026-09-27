import struct
import unittest

from pc.acrylic_pan_monitor.calibrate import (
    AREA_COUNT,
    CalibrationClient,
    DeviceError,
    run_calibration,
)
from pc.acrylic_pan_monitor.protocol import (
    CALIBRATION_ACK_PAYLOAD,
    CALIBRATION_RESULT_PAYLOAD,
    CalibrationOp,
    CalibrationSource,
    Frame,
    FrameStreamDecoder,
    MessageType,
    ProtocolError,
    decode_calibration_ack,
    decode_calibration_result,
    encode_frame,
)


def result_frame(target, predicted, *, flags=1, source=2, count=1, sequence=9):
    outputs = [0.0] * 12
    outputs[predicted] = 1.0
    payload = CALIBRATION_RESULT_PAYLOAD.pack(
        target, predicted, flags, source, count, 0, 1234, 56000, *outputs
    )
    return Frame(MessageType.CALIBRATION_RESULT, sequence, payload)


class FakeBoard:
    """Firmware stand-in: wrong areas are recognised once they were trained."""

    def __init__(self, wrong_before=(), wrong_after=(), fail_on_train=False):
        self.decoder = FrameStreamDecoder()
        self.output = bytearray()
        self.wrong_before = set(wrong_before)
        self.wrong_after = set(wrong_after)
        self.fail_on_train = fail_on_train
        self.source = CalibrationSource.FACTORY
        self.saved_count = 0
        self.work_count = 0
        self.trained = set()
        self.operations = []

    def read(self, size):
        data, self.output = bytes(self.output[:size]), self.output[size:]
        return data

    def write(self, data):
        for frame in self.decoder.feed(data):
            self._handle(frame)

    def _send(self, frame):
        self.output += encode_frame(frame)

    def _ack(self, sequence, operation):
        payload = CALIBRATION_ACK_PAYLOAD.pack(
            MessageType.CALIBRATION, operation, self.source,
            int(self.source == CalibrationSource.SAVED or self.saved_count > 0),
            self.saved_count, self.work_count,
        )
        self._send(Frame(MessageType.ACK, sequence, payload))

    def _handle(self, frame):
        if frame.message_type == MessageType.SET_MODE:
            self._send(Frame(MessageType.ACK, frame.sequence,
                             bytes([MessageType.SET_MODE, frame.payload[0]])))
            return
        operation, argument = frame.payload
        self.operations.append(CalibrationOp(operation))
        if operation == CalibrationOp.BEGIN:
            self.source, self.work_count = CalibrationSource.WORKING, 0
        elif operation == CalibrationOp.COMMIT:
            self.source, self.saved_count = CalibrationSource.SAVED, self.work_count
            self.work_count = 0
        elif operation == CalibrationOp.DISCARD:
            self.source = (CalibrationSource.SAVED if self.saved_count
                           else CalibrationSource.FACTORY)
            self.work_count = 0
        self._ack(frame.sequence, operation)
        if operation != CalibrationOp.ARM:
            return
        if argument != 0xFF and self.fail_on_train and self.work_count == 3:
            self._send(Frame(MessageType.NACK, 50, bytes([MessageType.CALIBRATION_RESULT, 4])))
            return
        area = argument if argument != 0xFF else self._next_check_area()
        calibrated = self.source == CalibrationSource.WORKING and self.work_count > 0
        wrong = self.wrong_after if calibrated else self.wrong_before
        predicted = (area + 1) % AREA_COUNT if area in wrong else area
        flags = 0
        if argument != 0xFF:
            self.work_count += 1
            flags = 1
        self.output += encode_frame(result_frame(
            argument, predicted, flags=flags, source=self.source,
            count=self.work_count, sequence=100 + self.work_count,
        ))

    def _next_check_area(self):
        self.check_index = getattr(self, "check_index", -1) + 1
        return self.check_index % AREA_COUNT


class CalibrationProtocolTests(unittest.TestCase):
    def test_calibration_ack_and_result_decode(self):
        ack = Frame(MessageType.ACK, 3, CALIBRATION_ACK_PAYLOAD.pack(
            MessageType.CALIBRATION, CalibrationOp.COMMIT, CalibrationSource.SAVED, 1, 60, 0
        ))
        status = decode_calibration_ack(ack)
        self.assertEqual(status.operation, CalibrationOp.COMMIT)
        self.assertEqual(status.source, CalibrationSource.SAVED)
        self.assertTrue(status.saved_valid)
        self.assertEqual(status.saved_count, 60)

        result = decode_calibration_result(result_frame(4, 5, count=7))
        self.assertEqual((result.target, result.predicted_class), (4, 5))
        self.assertTrue(result.trained)
        self.assertFalse(result.training_failed)
        self.assertEqual(result.work_count, 7)
        self.assertEqual((result.inference_us, result.train_us), (1234, 56000))
        self.assertIsNone(decode_calibration_result(result_frame(0xFF, 2, flags=0)).target)

    def test_calibration_payload_validation(self):
        with self.assertRaises(ProtocolError):
            decode_calibration_result(result_frame(12, 0))
        with self.assertRaises(ProtocolError):
            decode_calibration_result(Frame(MessageType.CALIBRATION_RESULT, 1, b"\x00" * 8))
        with self.assertRaises(ProtocolError):
            decode_calibration_ack(Frame(MessageType.ACK, 1, struct.pack("<BB", 0x15, 4)))
        with self.assertRaises(ProtocolError):
            decode_calibration_ack(Frame(MessageType.ACK, 1, CALIBRATION_ACK_PAYLOAD.pack(
                MessageType.CALIBRATION, 0, 7, 0, 0, 0)))

    def test_guided_run_commits_when_checks_improve(self):
        board = FakeBoard(wrong_before={2, 7})
        report = run_calibration(CalibrationClient(board), 2, prompt=lambda _: None)
        self.assertEqual((report.before_correct, report.after_correct), (10, 12))
        self.assertEqual(report.training_hits, 2 * AREA_COUNT)
        self.assertEqual(report.decision, "committed")
        self.assertEqual(board.source, CalibrationSource.SAVED)
        self.assertEqual(board.saved_count, 2 * AREA_COUNT)
        self.assertEqual(board.operations[0], CalibrationOp.STATUS)
        self.assertIn(CalibrationOp.BEGIN, board.operations)

    def test_guided_run_discards_when_checks_get_worse(self):
        board = FakeBoard(wrong_after={1, 3, 5})
        report = run_calibration(CalibrationClient(board), 1, prompt=lambda _: None)
        self.assertEqual((report.before_correct, report.after_correct), (12, 9))
        self.assertEqual(report.decision, "discarded")
        self.assertEqual(board.source, CalibrationSource.FACTORY)
        self.assertNotIn(CalibrationOp.COMMIT, board.operations)

    def test_failure_mid_run_discards_working_calibration(self):
        board = FakeBoard(fail_on_train=True)
        with self.assertRaises(DeviceError):
            run_calibration(CalibrationClient(board), 1, prompt=lambda _: None)
        self.assertEqual(board.operations[-1], CalibrationOp.DISCARD)
        self.assertEqual(board.source, CalibrationSource.FACTORY)


if __name__ == "__main__":
    unittest.main()
