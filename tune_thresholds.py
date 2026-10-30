"""Tune per-label minimum span scores on the VALIDATION split and write them to
<model>/thresholds.json (used automatically by Predictor / evaluate / predict).

Never tune on test: that would make the test numbers optimistic.

Recall-first: for each label, pick the threshold with the best strict F1 among
thresholds that lose at most --max-recall-drop recall versus no threshold.
A missed secret is worse than a false alarm.

Usage:
  python tune_thresholds.py --model runs/deberta_v0_run1/best --data data/v0/val.jsonl
"""
import argparse
import json
from pathlib import Path

import numpy as np

from common import Predictor, load_labels, read_jsonl
from evaluate import count_doc, prf

GRID = [0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--data", required=True, help="validation JSONL")
    ap.add_argument("--max-recall-drop", type=float, default=0.01)
    ap.add_argument("--batch-docs", type=int, default=32)
    args = ap.parse_args()

    labels = load_labels(Path(args.model) / "labels.yaml")
    rows = read_jsonl(args.data)
    pred = Predictor(args.model)
    preds = []
    for i in range(0, len(rows), args.batch_docs):
        preds += pred.predict([r["text"] for r in rows[i:i + args.batch_docs]], use_thresholds=False)

    thresholds = {"default": 0.0}
    print(f"{'label':<15}{'thr':>5} {'P':>7} {'R':>7} {'F1':>7}   (no threshold: P / R / F1)")
    for li, lab in enumerate(labels):
        results = []
        for t in GRID:
            kept = [[sp for sp in p if sp["label"] != lab or sp["score"] >= t] for p in preds]
            c = np.sum([count_doc(r["spans"], k, labels, "strict") for r, k in zip(rows, kept)], axis=0)
            p, r, f = prf(c[li])
            results.append((t, float(p), float(r), float(f)))
        base = results[0]
        allowed = [x for x in results if x[2] >= base[2] - args.max_recall_drop]
        best = max(allowed, key=lambda x: (round(x[3], 4), -x[0]))
        thresholds[lab] = best[0]
        print(f"{lab:<15}{best[0]:>5.2f} {best[1]:>7.3f} {best[2]:>7.3f} {best[3]:>7.3f}   "
              f"({base[1]:.3f} / {base[2]:.3f} / {base[3]:.3f})")

    out = Path(args.model) / "thresholds.json"
    out.write_text(json.dumps(thresholds, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
