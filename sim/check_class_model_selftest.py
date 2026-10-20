"""Compare the 12-area model on the board with the PC reference, using the golden inputs of the model header.

The AxlCORE generates alpha from the seed and scale (the firmware carries no alpha copy); if that alpha differed from
the one the model was trained with, these outputs would not match the PC reference computed with the header's alpha.

    python -m sim.check_class_model_selftest --port COM3
"""

from __future__ import annotations

import argparse
import re
import struct
from pathlib import Path

import numpy as np

from .dummy_model_pipeline import from_bfloat16_bits, mcu_reference
from .odl_engine_experiment import MODEL_HEADER, load_model

GOLDEN_CASE_BASE = 0x90
CASE_COUNT = 12


def golden_inputs(header: Path = MODEL_HEADER) -> np.ndarray:
    text = header.read_text(encoding="ascii")
    match = re.search(r"apan_model_golden_inputs\[[^\]]*\]\[[^\]]*\] = \{(.*?)\};", text, re.S)
    bits = [int(value, 16) for value in re.findall(r"0x[0-9A-Fa-f]{4}", match.group(1))]
    return np.asarray(bits, dtype=np.uint16).reshape(CASE_COUNT, 128)


def run(port_name: str) -> dict:
    import serial
    from pc.acrylic_pan_monitor.calibrate import CalibrationClient
    from pc.acrylic_pan_monitor.protocol import Frame, MessageType, encode_frame

    model = load_model()
    inputs = golden_inputs()
    reference = mcu_reference(from_bfloat16_bits(inputs), model["alpha"].astype(np.float32),
                              from_bfloat16_bits(model["beta_bits"]).astype(np.float32))
    results = []
    with serial.Serial(port_name, 115_200, timeout=0.05, write_timeout=0.5) as port:
        client = CalibrationClient(port)
        # A working calibration starts from the factory beta (the saved one is not touched) and is discarded after.
        from pc.acrylic_pan_monitor.protocol import CalibrationOp
        client.set_calibration_mode()
        saved = client.calibration(CalibrationOp.STATUS)
        status = client.calibration(CalibrationOp.BEGIN)
        for case in range(CASE_COUNT):
            sequence = client._take_sequence()
            frame = client._request(encode_frame(Frame(
                MessageType.AI_SELFTEST, sequence, bytes([GOLDEN_CASE_BASE + case]))), sequence)
            if frame.message_type != MessageType.POSITION_DIAGNOSTIC:
                raise RuntimeError(f"unexpected reply: {frame.message_type!r}")
            device = np.asarray(struct.unpack("<12f", frame.payload[4:4 + 48]))
            results.append({"case": case, "device_class": int(frame.payload[1]),
                            "reference_class": int(np.argmax(reference[case])),
                            "max_abs_diff": float(np.abs(device - reference[case]).max()),
                            "max_abs_output": float(np.abs(reference[case]).max())})
        client.calibration(CalibrationOp.DISCARD)
    print(f"saved calibration before: {saved.saved_valid} ({saved.saved_count} hits); beta under test: {status.source.name}"
          " (factory beta, nothing saved)")
    for row in results:
        print(f"case {row['case']:2d}: device class {row['device_class']:2d}, reference class "
              f"{row['reference_class']:2d}, max |difference| {row['max_abs_diff']:.4f} "
              f"(max |output| {row['max_abs_output']:.2f})")
    return {"source": status.source.name, "cases": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True)
    run(parser.parse_args().port)


if __name__ == "__main__":
    main()
