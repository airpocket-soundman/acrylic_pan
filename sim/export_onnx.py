"""Export the deployed 12-class model and its preprocessing as an ONNX graph.

The graph mirrors the firmware inference path (``sim.real_model_pipeline`` and
``sim.dummy_model_pipeline.mcu_reference``) so it can be inspected in Netron.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

from .real_model_pipeline import POSTTRIGGER_COUNT, SAMPLE_COUNT, TRIGGER_INDEX, time_sample_indices

DEFAULT_MODEL = Path("artifacts/real_model_400x300x5_12class_max_data_20260828/model.npz")
DEFAULT_OUTPUT = Path("web/assets/apan_12class_model.onnx")


def _bf16_to_float32(bits: np.ndarray) -> np.ndarray:
    return (np.asarray(bits).astype(np.uint16).astype(np.uint32) << 16).view(np.float32)


def build_graph(model_path: Path) -> onnx.ModelProto:
    with np.load(model_path, allow_pickle=False) as archive:
        alpha = _bf16_to_float32(archive["alpha_bf16"])
        beta = _bf16_to_float32(archive["beta_bf16"])
        mean = archive["feature_mean"].astype(np.float32)
        scale = archive["feature_scale"].astype(np.float32)

    def const(name: str, value: np.ndarray) -> onnx.TensorProto:
        return numpy_helper.from_array(np.asarray(value), name)

    initializers = [
        const("pre_start", np.array([0], np.int64)), const("pre_end", np.array([TRIGGER_INDEX], np.int64)),
        const("post_start", np.array([TRIGGER_INDEX], np.int64)),
        const("post_end", np.array([SAMPLE_COUNT], np.int64)), const("axis1", np.array([1], np.int64)),
        const("time_sample_indices", time_sample_indices().astype(np.int64)),
        const("peak_floor", np.array(1.0, np.float32)),
        const("feature_mean", mean), const("feature_scale", scale),
        const("alpha", alpha), const("beta", beta),
    ]
    nodes = [
        helper.make_node("Slice", ["waveform", "pre_start", "pre_end", "axis1"], ["pretrigger"], name="Slice_pretrigger"),
        helper.make_node("Slice", ["waveform", "post_start", "post_end", "axis1"], ["posttrigger"], name="Slice_posttrigger"),
        helper.make_node("ReduceMean", ["pretrigger"], ["baseline"], axes=[1], keepdims=1, name="ReduceMean_baseline"),
        helper.make_node("Sub", ["posttrigger", "baseline"], ["centered"], name="Sub_baseline"),
        helper.make_node("Gather", ["centered", "time_sample_indices"], ["sampled"], axis=1, name="Gather_128"),
        helper.make_node("Abs", ["sampled"], ["abs_sampled"], name="Abs"),
        helper.make_node("ReduceMax", ["centered"], ["peak_raw"], axes=[1], keepdims=1, name="ReduceMax_peak"),
        helper.make_node("Max", ["peak_raw", "peak_floor"], ["peak"], name="Max_peak_floor"),
        helper.make_node("Div", ["sampled", "peak"], ["normalized"], name="Div_normalize"),
        helper.make_node("Sub", ["normalized", "feature_mean"], ["shifted"], name="Sub_mean"),
        helper.make_node("Div", ["shifted", "feature_scale"], ["features"], name="Div_std"),
        helper.make_node("Cast", ["features"], ["features_bf16"], to=TensorProto.BFLOAT16, name="Cast_bfloat16_in"),
        helper.make_node("Cast", ["features_bf16"], ["features_f32"], to=TensorProto.FLOAT, name="Cast_float32"),
        helper.make_node("MatMul", ["features_f32", "alpha"], ["projection"], name="MatMul_alpha"),
        helper.make_node("HardSigmoid", ["projection"], ["hidden"], alpha=0.2, beta=0.5, name="HardSigmoid"),
        helper.make_node("MatMul", ["hidden", "beta"], ["scores"], name="MatMul_beta"),
        helper.make_node("ArgMax", ["scores"], ["area"], axis=1, keepdims=0, name="ArgMax_area"),
    ]
    # Abs is kept out of the dataflow: the peak is taken from |centered| below.
    nodes[5] = helper.make_node("Abs", ["centered"], ["abs_centered"], name="Abs")
    nodes[6] = helper.make_node("ReduceMax", ["abs_centered"], ["peak_raw"], axes=[1], keepdims=1, name="ReduceMax_peak")
    graph = helper.make_graph(
        nodes, "acrylic_pan_12class",
        [helper.make_tensor_value_info("waveform", TensorProto.FLOAT, [1, SAMPLE_COUNT])],
        [helper.make_tensor_value_info("scores", TensorProto.FLOAT, [1, 12]),
         helper.make_tensor_value_info("area", TensorProto.INT64, [1])],
        initializers,
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)], producer_name="acrylic_pan")
    model.ir_version = 8
    model.doc_string = (f"Input 128, hidden 32, output 12; post-trigger samples {POSTTRIGGER_COUNT}; "
                        "alpha from official Solist-AI Simulator seed 1.")
    return model


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    model = build_graph(args.model)
    onnx.checker.check_model(model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, args.output)
    print(args.output)


if __name__ == "__main__":
    main()
