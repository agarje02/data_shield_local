"""Preflight check for a dataset version. Run locally BEFORE uploading to Drive,
so no Colab GPU time is wasted on broken data.

Usage:
  python data/validate_dataset.py data/v0
Exit code 1 if any error is found.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import load_labels, read_jsonl  # noqa: E402

SPLITS = ("train", "val", "test")


def check_row(row, labels):
    errs = []
    for key in ("id", "text", "spans"):
        if key not in row:
            errs.append(f"missing key '{key}'")
    if errs:
        return errs
    text = row["text"]
    if not text.strip():
        errs.append("empty text")
    prev_end = -1
    for sp in sorted(row["spans"], key=lambda s: s["start"]):
        s, e, lab = sp.get("start"), sp.get("end"), sp.get("label")
        if lab not in labels:
            errs.append(f"unknown label {lab!r}")
        if not (isinstance(s, int) and isinstance(e, int) and 0 <= s < e <= len(text)):
            errs.append(f"bad offsets {s}-{e} (len {len(text)})")
            continue
        if "value" in sp and text[s:e] != sp["value"]:
            errs.append(f"text[{s}:{e}]={text[s:e]!r} != value {sp['value']!r}")
        if text[s:e] != text[s:e].strip():
            errs.append(f"span {s}-{e} has leading/trailing whitespace")
        if s < prev_end:
            errs.append(f"overlapping spans at {s}")
        prev_end = max(prev_end, e)
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("--labels", default=str(Path(__file__).resolve().parent.parent / "configs" / "labels.yaml"))
    ap.add_argument("--min-eval-spans", type=int, default=20,
                    help="warn when a label has fewer spans than this in val/test")
    args = ap.parse_args()

    labels = load_labels(args.labels)
    data_dir = Path(args.data_dir)
    n_errors, n_warn = 0, 0
    seen_ids = set()

    for split in SPLITS:
        path = data_dir / f"{split}.jsonl"
        if not path.exists():
            print(f"ERROR {split}: {path} not found")
            n_errors += 1
            continue
        rows = read_jsonl(path)
        counts = Counter()
        for i, row in enumerate(rows):
            for err in check_row(row, labels):
                n_errors += 1
                if n_errors <= 50:
                    print(f"ERROR {split}:{i} ({row.get('id')}): {err}")
            rid = row.get("id")
            if rid in seen_ids:
                n_errors += 1
                print(f"ERROR {split}:{i}: duplicate id {rid}")
            seen_ids.add(rid)
            counts.update(sp.get("label") for sp in row.get("spans", []))

        print(f"\n{split}: {len(rows)} rows, {sum(counts.values())} spans")
        for lab in labels:
            c = counts.get(lab, 0)
            flag = ""
            if c == 0 and split == "train":
                flag = "  <-- ERROR: no training examples"
                n_errors += 1
            elif split != "train" and c < args.min_eval_spans:
                flag = f"  <-- WARN: < {args.min_eval_spans} spans, score will be noisy"
                n_warn += 1
            print(f"  {lab:<15} {c:>6}{flag}")

    print(f"\n{n_errors} error(s), {n_warn} warning(s)")
    sys.exit(1 if n_errors else 0)


if __name__ == "__main__":
    main()
