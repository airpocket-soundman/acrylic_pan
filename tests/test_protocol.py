import unittest
import struct

import numpy as np

from pc.acrylic_pan_monitor.protocol import (
    EventData,
    EventAssembler,
    EVENT_CHUNK_HEADER,
    AI_RESULT_PAYLOAD,
    Frame,
    FrameStreamDecoder,
    MessageType,
    ProtocolError,
    cobs_decode,
    cobs_encode,
    decode_event,
    decode_event_chunk,
    decode_ai_result,
    decode_inference_event,
    decode_frame,
    encode_event_payload,
    encode_frame,
)
from pc.acrylic_pan_monitor.signal_processing import prepare_plot_data


class ProtocolTests(unittest.TestCase):
    @staticmethod
    def make_chunk(event_id, chunk_index, samples, *, total=2048, sequence=0):
        count = (total + 511) // 512
        payload = EVENT_CHUNK_HEADER.pack(
            event_id, chunk_index, count, 25_600, total, 64, 3000, len(samples)
        ) + struct.pack(f"<{len(samples)}h", *samples)
        return decode_event_chunk(Frame(MessageType.EVENT_CHUNK, sequence, payload))
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

    def test_inference_event_contains_class_and_source_waveform(self):
        samples = (-20, 0, 120, -80, 10)
        outputs = (0.1, 0.2, 0.8, 0.0, -0.1, 0.1, 0.2, 0.3)
        event_header = struct.pack("<IHHHH", 25_600, len(samples), 2, 120, 0)
        ai_payload = AI_RESULT_PAYLOAD.pack(0xFF, 2, 0, *outputs)
        frame = Frame(
            MessageType.INFERENCE_EVENT, 99,
            event_header + ai_payload + struct.pack("<5h", *samples),
        )
        combined = decode_inference_event(frame)
        self.assertEqual(combined.result.predicted_class, 2)
        self.assertEqual(combined.result.sequence, 99)
        self.assertEqual(combined.event.samples, samples)
        self.assertEqual(combined.event.trigger_index, 2)

    def test_long_event_reassembles_out_of_order_once(self):
        source = tuple(range(2048))
        assembler = EventAssembler()
        result = None
        for index in (2, 0, 3, 1):
            chunk = self.make_chunk(7, index, source[index * 512:(index + 1) * 512],
                                    sequence=100 + index)
            result = assembler.feed(chunk) or result
        self.assertIsNotNone(result)
        self.assertEqual(result.sequence, 7)
        self.assertEqual(result.samples, source)
        self.assertEqual(assembler.completed, 1)
        self.assertIsNone(assembler.feed(self.make_chunk(7, 0, source[:512])))
        self.assertEqual(assembler.completed, 1)

    def test_long_event_duplicate_conflict_and_timeout(self):
        now = [0.0]
        assembler = EventAssembler(timeout_seconds=2.0, clock=lambda: now[0])
        original = self.make_chunk(8, 0, tuple(range(512)))
        self.assertIsNone(assembler.feed(original))
        self.assertIsNone(assembler.feed(original))
        self.assertEqual(assembler.duplicates, 1)
        conflicting = self.make_chunk(8, 0, tuple(reversed(range(512))))
        with self.assertRaises(ProtocolError):
            assembler.feed(conflicting)
        self.assertEqual(assembler.conflicts, 1)
        self.assertIsNone(assembler.feed(self.make_chunk(9, 0, tuple(range(512)))))
        now[0] = 2.1
        self.assertEqual(assembler.expire(), [9])
        self.assertEqual(assembler.timed_out, 1)

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
