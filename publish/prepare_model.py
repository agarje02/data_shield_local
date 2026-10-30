"""Prepare the model folder for a Hugging Face model repo (nothing is uploaded here).

  python publish/prepare_model.py [--model models/deberta_v2_3_onnx] [--out publish/hf_model]

Copies the ONNX model, tokenizer, labels, thresholds and manifest, adds the model card
(publish/MODEL_CARD.md -> README.md) and SHA256SUMS, which `datashield-local download-model` checks.
Upload afterwards with:  huggingface-cli upload <user>/<repo> publish/hf_model .
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from datashield_local.engine.model_hub import verify, write_sums  # noqa: E402

KEEP = {"model.onnx", "tokenizer.json", "tokenizer_config.json", "config.json", "labels.yaml",
        "thresholds.json", "manifest.json", "onnx_export.json"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(ROOT / "models" / "deberta_v2_3_onnx"))
    ap.add_argument("--out", default=str(ROOT / "publish" / "hf_model"))
    args = ap.parse_args()
    src, out = Path(args.model), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for f in sorted(src.iterdir()):
        if f.name in KEEP:
            shutil.copy2(f, out / f.name)
    shutil.copy2(ROOT / "publish" / "MODEL_CARD.md", out / "README.md")
    n = write_sums(out)
    verify(out)
    size = sum(f.stat().st_size for f in out.iterdir()) / 1e6
    print(f"{out}: {n} files, {size:.0f} MB, SHA256SUMS written and verified")


if __name__ == "__main__":
    main()
