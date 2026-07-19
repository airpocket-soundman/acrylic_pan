from pathlib import Path
import sys

from pc.acrylic_pan_monitor.protocol import decode_event_chunk, decode_frame

packet = Path(sys.argv[1]).read_bytes()
frame = decode_frame(packet)
chunk = decode_event_chunk(frame)
assert frame.sequence == 42
assert frame.timestamp_us == 123456
assert chunk.event_id == 9
assert chunk.sample_rate_hz == 25600
assert chunk.total_samples == 2048
assert chunk.trigger_index == 64
assert chunk.peak_abs == 2000
assert chunk.chunk_index == 0
assert chunk.chunk_count == 4
assert len(chunk.samples) == 512
assert chunk.samples[:64] == (10,) * 64
assert chunk.samples[64:67] == (2000, 1, 2)
assert chunk.samples[511] == 447
print("firmware long capture and APAN/Python compatibility: OK")
