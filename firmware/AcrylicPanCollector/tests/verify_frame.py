from pathlib import Path
import sys

from pc.acrylic_pan_monitor.protocol import decode_event, decode_frame

packet = Path(sys.argv[1]).read_bytes()
frame = decode_frame(packet)
event = decode_event(frame)
assert frame.sequence == 42
assert frame.timestamp_us == 123456
assert event.sample_rate_hz == 25600
assert event.trigger_index == 128
assert event.peak_abs == 2000
assert len(event.samples) == 512
assert event.samples[:128] == (10,) * 128
assert event.samples[128:131] == (2000, 1, 2)
assert event.samples[-1] == 383
print("firmware capture and APAN/Python compatibility: OK")
