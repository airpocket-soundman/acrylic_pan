import unittest

import numpy as np

from pc.acrylic_pan_monitor.protocol import (
    EventData,
    AI_RESULT_PAYLOAD,
    Frame,
    FrameStreamDecoder,
    MessageType,
    ProtocolError,
    cobs_decode,
    cobs_encode,
    decode_event,
    decode_ai_result,
    decode_frame,
    encode_event_payload,
    encode_frame,
)
from pc.acrylic_pan_monitor.signal_processing import prepare_plot_data


class ProtocolTests(unittest.TestCase):
    def test_cobs_round_trip_with_zeroes(self):
        raw = bytes(range(256)) + b"\x00\x00payload"
        self.assertEqual(cobs_decode(cobs_encode(raw)), raw)

    def test_event_round_trip_and_stream_splitting(self):
        event = EventData(25_600, 3, 3200, (-1, 0, 1, 3200, -400))
        packet = encode_frame(Frame(MessageType.EVENT_DATA, 42, encode_event_payload(event), flags=3))
        decoder = FrameStreamDecoder()
        frames = decoder.feed(packet[:7]) + decoder.feed(packet[7:])
        self.assertEqual(len(frames), 1)
        decoded = decode_event(frames[0])
        self.assertEqual(decoded.sequence, 42)
        self.assertEqual(decoded.sample_rate_hz, 25_600)
        self.assertEqual(decoded.samples, event.samples)
        self.assertEqual(decoded.flags, 3)

    def test_crc_error_is_rejected(self):
        packet = bytearray(encode_frame(Frame(MessageType.HELLO, 1, b"collector")))
        packet[4] ^= 0x20
        with self.assertRaises(ProtocolError):
            decode_frame(bytes(packet))

    def test_ai_result_decodes_eight_float_outputs(self):
        outputs = (0.1, 0.2, 0.3, 0.9, -0.1, 0.0, 0.4, 0.5)
        frame = Frame(MessageType.AI_RESULT, 17, AI_RESULT_PAYLOAD.pack(6, 3, 0, *outputs))
        result = decode_ai_result(frame)
        self.assertEqual(result.case_id, 6)
        self.assertEqual(result.predicted_class, 3)
        self.assertEqual(result.sequence, 17)
        self.assertEqual(len(result.outputs), 8)
        self.assertAlmostEqual(result.outputs[3], 0.9, places=6)

    def test_fft_peak(self):
        sample_rate = 25_600
        count = 512
        frequency = 1_000
        samples = tuple((10_000 * np.sin(2 * np.pi * frequency * np.arange(count) / sample_rate)).astype(np.int16))
        event = EventData(sample_rate, 100, 10_000, samples)
        plot = prepare_plot_data(event)
        peak_frequency = plot.frequency_hz[np.argmax(plot.magnitude_db[1:]) + 1]
        self.assertLessEqual(abs(peak_frequency - frequency), sample_rate / count)


if __name__ == "__main__":
    unittest.main()
