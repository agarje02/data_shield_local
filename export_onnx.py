"""Export a trained model to ONNX (full precision, fp32) and check it matches PyTorch.

Writes <out>/ with model.onnx plus everything Predictor needs (tokenizer,
config.json, labels.yaml, thresholds.json, manifest.json). Predictor picks
model.onnx automatically, so evaluate.py / predict.py / the local app work on it
unchanged.

Usage:
  python export_onnx.py --model runs/deberta_v1_run1/best --out runs/deberta_v1_run1/onnx
"""
import argparse
import json
import shutil
from pathlib import Path

import numpy as np
import torch
import transformers
from transformers import AutoModelForTokenClassification, AutoTokenizer

COPY = ["labels.yaml", "thresholds.json", "manifest.json"]
SAMPLES = [
    "Hi, I'm Priya Sharma, call me on +91 98765 43210 or mail priya.s@gmail.com.",
    "AWS_SECRET=AKIAIOSFODNN7ABCDEFG\nDB_PASSWORD=Summer@2024",
    "Order #48213977 shipped via AWB 3829104477, version 2.4.1, port 5432.",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--opset", type=int, default=17)
    ap.add_argument("--atol", type=float, default=1e-3, help="max allowed |logit difference|")
    args = ap.parse_args()

    src, out = Path(args.model), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dtype_key = "dtype" if int(transformers.__version__.split(".")[0]) >= 5 else "torch_dtype"
    model = AutoModelForTokenClassification.from_pretrained(src, **{dtype_key: torch.float32}).eval()
    tok = AutoTokenizer.from_pretrained(src)

    class Wrapper(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask):
            return self.m(input_ids=input_ids, attention_mask=attention_mask).logits

    enc = tok(SAMPLES, padding=True, return_tensors="pt")
    onnx_path = out / "model.onnx"
    # .eval() on the wrapper: the exporter restores its mode afterwards, and a
    # train-mode wrapper would switch dropout back on for the parity check below
    torch.onnx.export(
        Wrapper(model).eval(), (enc["input_ids"], enc["attention_mask"]), str(onnx_path),
        input_names=["input_ids", "attention_mask"], output_names=["logits"],
        dynamic_axes={"input_ids": {0: "batch", 1: "seq"}, "attention_mask": {0: "batch", 1: "seq"},
                      "logits": {0: "batch", 1: "seq"}},
        opset_version=args.opset, dynamo=False,
    )
    tok.save_pretrained(out)
    model.config.save_pretrained(out)
    for name in COPY:
        if (src / name).exists():
            shutil.copy(src / name, out / name)

    # parity: same inputs through PyTorch and ONNX Runtime, including a 512-token window
    import onnxruntime as ort
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    long = tok([" ".join(SAMPLES * 40)], truncation=True, max_length=512, return_tensors="pt")
    worst = 0.0
    for e in (enc, long):
        with torch.no_grad():
            ref = model(input_ids=e["input_ids"], attention_mask=e["attention_mask"]).logits.numpy()
        got = sess.run(["logits"], {"input_ids": e["input_ids"].numpy(),
                                    "attention_mask": e["attention_mask"].numpy()})[0]
        mask = e["attention_mask"].numpy().astype(bool)
        worst = max(worst, float(np.abs(ref - got)[mask].max()))
        if not (ref.argmax(-1)[mask] == got.argmax(-1)[mask]).all():
            raise SystemExit("ONNX predicts different tags than PyTorch")
    size_mb = onnx_path.stat().st_size / 1e6 + sum(p.stat().st_size for p in out.glob("model.onnx.data")) / 1e6
    report = {"source": str(src), "opset": args.opset, "max_abs_logit_diff": worst,
              "same_tags": True, "size_mb": round(size_mb, 1)}
    (out / "onnx_export.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if worst > args.atol:
        raise SystemExit(f"logit difference {worst:.2e} above tolerance {args.atol}")


if __name__ == "__main__":
    main()
