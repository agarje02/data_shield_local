"""Make smaller variants of an exported fp32 ONNX model (see export_onnx.py).

  int8 - dynamic quantization: weights stored as int8, activations quantized at
         run time. ~4x smaller, made for CPU.
  fp16 - weights and math in float16 (inputs/outputs stay int64/float32).
         ~2x smaller; fast on GPU, CPU support depends on onnxruntime.
  q4   - 4-bit weight-only linear layers (MatMulNBits, blocks of 32; activations stay fp32, so
         the FFN-output outliers that break int8 do not matter) plus an int8 embedding table.
         Smallest; runs in onnxruntime-web (WASM / WebGPU) for the browser extension.

Writes <out>/ with model.onnx plus the tokenizer/config/labels/thresholds files
copied from the source, so Predictor, evaluate.py and the app use it unchanged.
Checks the variant against fp32 on a few samples and records how many tags agree.

Usage:
  python quantize_onnx.py --src models/deberta_v1_onnx --out models/deberta_v1_onnx_int8 --mode int8
"""
import argparse
import json
import shutil
from pathlib import Path

import numpy as np

# same as export_onnx.SAMPLES; not imported because that module loads torch
SAMPLES = [
    "Hi, I'm Priya Sharma, call me on +91 98765 43210 or mail priya.s@gmail.com.",
    "AWS_SECRET=AKIAIOSFODNN7ABCDEFG\nDB_PASSWORD=Summer@2024",
    "Order #48213977 shipped via AWB 3829104477, version 2.4.1, port 5432.",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="folder with the fp32 model.onnx")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=["int8", "fp16", "q4"], required=True)
    ap.add_argument("--block-size", type=int, default=32, help="q4: weights per quantization block")
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_file() and not f.name.startswith("model.onnx") and f.name != "onnx_export.json":
            shutil.copy(f, out / f.name)
    dst = out / "model.onnx"

    if args.mode == "int8":
        import onnx
        from onnxruntime.quantization import QuantType, quantize_dynamic
        # Quantize the linear layers and the embedding table, except each block's
        # FFN output layer (the one after GELU): its inputs have large outliers and
        # int8 there flips ~half the tags. Measured per layer group on v1: every
        # other group keeps 100% tag agreement with fp32.
        graph = onnx.load(str(src / "model.onnx"), load_external_data=False).graph
        weights = {i.name for i in graph.initializer}
        nodes = [n.name for n in graph.node
                 if (n.op_type == "MatMul" and n.input[1] in weights
                     and not ("/output/" in n.name and "attention" not in n.name))
                 or (n.op_type == "Gather" and n.input[0] in weights)]
        del graph
        quantize_dynamic(str(src / "model.onnx"), str(dst), weight_type=QuantType.QInt8,
                         nodes_to_quantize=nodes)
    elif args.mode == "q4":
        import onnx
        from onnxruntime.quantization import QuantType, quantize_dynamic
        from onnxruntime.quantization.matmul_nbits_quantizer import MatMulNBitsQuantizer
        graph = onnx.load(str(src / "model.onnx"), load_external_data=False).graph
        weights = {i.name for i in graph.initializer}
        gathers = [n.name for n in graph.node if n.op_type == "Gather" and n.input[0] in weights]
        del graph
        tmp = out / "model.gather_int8.onnx"
        quantize_dynamic(str(src / "model.onnx"), str(tmp), weight_type=QuantType.QInt8, nodes_to_quantize=gathers)
        q = MatMulNBitsQuantizer(onnx.load(str(tmp)), bits=4, block_size=args.block_size, is_symmetric=True,
                                 op_types_to_quantize=("MatMul",))
        q.process()
        q.model.save_model_to_file(str(dst), use_external_data_format=False)
        del q
        tmp.unlink()
    else:
        import onnx
        # onnxruntime's converter; onnxconverter_common's leaves Cast nodes typed fp32
        from onnxruntime.transformers.float16 import convert_float_to_float16
        model = convert_float_to_float16(onnx.load(str(src / "model.onnx")), keep_io_types=True)
        onnx.save(model, str(dst))
        del model

    # agreement with fp32 on the export samples plus a full 512-token window
    import onnxruntime as ort
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(out)
    batches = [tok(SAMPLES, padding=True, return_tensors="np"),
               tok([" ".join(SAMPLES * 40)], truncation=True, max_length=512, return_tensors="np")]
    logits = {}
    for name, path in (("fp32", src / "model.onnx"), (args.mode, dst)):
        sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        logits[name] = [sess.run(["logits"], {"input_ids": b["input_ids"].astype(np.int64),
                                              "attention_mask": b["attention_mask"].astype(np.int64)})[0]
                        for b in batches]
        del sess
    same = total = 0
    worst = 0.0
    for b, ref, got in zip(batches, logits["fp32"], logits[args.mode]):
        mask = b["attention_mask"].astype(bool)
        same += int((ref.argmax(-1)[mask] == got.argmax(-1)[mask]).sum())
        total += int(mask.sum())
        worst = max(worst, float(np.abs(ref - got)[mask].max()))
    report = {"source": str(src), "mode": args.mode, "size_mb": round(dst.stat().st_size / 1e6, 1),
              "tag_agreement_vs_fp32": round(same / total, 4), "max_abs_logit_diff": worst}
    (out / "onnx_export.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
