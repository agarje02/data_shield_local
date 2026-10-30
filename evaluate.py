"""Span-level evaluation of a trained model on a JSONL test set.

Uses the same sliding-window Predictor as predict.py, so the numbers reflect
what the model really does on full documents.

Reports per label: strict match (same start, end, label) and partial match
(same label, overlapping), plus a label-agnostic "detected" score (was the
sensitive text flagged at all, whatever the type), with 95% bootstrap CIs.

Usage:
  python evaluate.py --model runs/deberta_v0_run1/best --data data/v0/test.jsonl \
      --report reports/deberta_v0_run1.json
"""
import argparse
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np

from common import Predictor, load_labels, read_jsonl

TP, FP, FN = 0, 1, 2


def overlaps(a, b):
    return a["start"] < b["end"] and b["start"] < a["end"]


def match(gold, pred, mode):
    """Returns (matched_pred_idx, matched_gold_idx) sets for one document."""
    used_g, used_p = set(), set()
    for pi, p in enumerate(pred):
        for gi, g in enumerate(gold):
            if gi in used_g:
                continue
            if mode == "strict":
                ok = p["label"] == g["label"] and p["start"] == g["start"] and p["end"] == g["end"]
            elif mode == "partial":
                ok = p["label"] == g["label"] and overlaps(p, g)
            else:  # detected: any label
                ok = overlaps(p, g)
            if ok:
                used_g.add(gi)
                used_p.add(pi)
                break
    return used_p, used_g


def count_doc(gold, pred, labels, mode):
    """Counts array (n_labels + 1, 3); last row is the label-agnostic total."""
    li = {l: i for i, l in enumerate(labels)}
    c = np.zeros((len(labels) + 1, 3), dtype=np.int64)
    mp, mg = match(gold, pred, mode)
    # TP/FN are attributed to the gold label, FP to the predicted label
    # (identical for strict/partial; matters for label-agnostic "detected")
    for gi, g in enumerate(gold):
        c[li[g["label"]], TP if gi in mg else FN] += 1
    for pi, p in enumerate(pred):
        if pi not in mp and p["label"] in li:
            c[li[p["label"]], FP] += 1
    c[-1] = c[:-1].sum(axis=0)
    return c


def prf(c):
    """c[..., 3] -> precision, recall, f1 arrays."""
    tp, fp, fn = c[..., TP].astype(float), c[..., FP].astype(float), c[..., FN].astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        p = np.where(tp + fp > 0, tp / (tp + fp), 0.0)
        r = np.where(tp + fn > 0, tp / (tp + fn), 0.0)
        f = np.where(p + r > 0, 2 * p * r / (p + r), 0.0)
    return p, r, f


def bootstrap_f1(per_doc, n_boot, seed):
    """per_doc: (docs, n_labels+1, 3). Returns (low, high) arrays of F1 per row."""
    rng = np.random.default_rng(seed)
    n = per_doc.shape[0]
    f1s = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        f1s.append(prf(per_doc[idx].sum(axis=0))[2])
    f1s = np.stack(f1s)
    return np.percentile(f1s, 2.5, axis=0), np.percentile(f1s, 97.5, axis=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--data", required=True, help="JSONL in our format (text + spans)")
    ap.add_argument("--report", help="where to write the JSON report")
    ap.add_argument("--labels", help="labels.yaml (default: the one saved with the model)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch-docs", type=int, default=32)
    ap.add_argument("--n-boot", type=int, default=1000)
    ap.add_argument("--no-thresholds", action="store_true")
    ap.add_argument("--no-validators", action="store_true", help="model only, no checksum/format rules")
    ap.add_argument("--max-examples", type=int, default=50)
    ap.add_argument("--only-labels", help="comma-separated labels to score; gold and predicted spans of "
                                          "other labels are dropped (compare models with different label sets)")
    args = ap.parse_args()

    labels = load_labels(args.labels or Path(args.model) / "labels.yaml")
    rows = read_jsonl(args.data)
    if args.limit:
        rows = rows[:args.limit]
    if args.only_labels:
        keep = args.only_labels.split(",")
        labels = [l for l in labels if l in keep]
        rows = [{**r, "spans": [s for s in r["spans"] if s["label"] in labels]} for r in rows]

    pred = Predictor(args.model)
    t0 = time.time()
    preds = []
    for i in range(0, len(rows), args.batch_docs):
        preds += pred.predict([r["text"] for r in rows[i:i + args.batch_docs]],
                              use_thresholds=not args.no_thresholds,
                              use_validators=not args.no_validators)
    secs = time.time() - t0
    preds = [[sp for sp in p if sp["label"] in labels] for p in preds]

    modes = ("strict", "partial", "detected")
    per_doc = {m: np.stack([count_doc(r["spans"], p, labels, m) for r, p in zip(rows, preds)]) for m in modes}

    report = {"model": str(args.model), "data": str(args.data), "docs": len(rows),
              "device": pred.device, "seconds": round(secs, 1),
              "ms_per_doc": round(1000 * secs / max(1, len(rows)), 1),
              "thresholds_used": not args.no_thresholds and bool(pred.thresholds),
              "validators_used": not args.no_validators}
    names = labels + ["ALL"]
    for m in modes:
        total = per_doc[m].sum(axis=0)
        p, r, f = prf(total)
        lo, hi = bootstrap_f1(per_doc[m], args.n_boot, seed=0)
        report[m] = {
            names[i]: {"precision": round(float(p[i]), 4), "recall": round(float(r[i]), 4),
                       "f1": round(float(f[i]), 4), "f1_ci95": [round(float(lo[i]), 4), round(float(hi[i]), 4)],
                       "gold_spans": int(total[i, TP] + total[i, FN]),
                       "pred_spans": int(total[i, TP] + total[i, FP])}
            for i in range(len(names))
        }

    # false positives on documents with no sensitive data at all (the real-world base rate)
    clean = [(r, p) for r, p in zip(rows, preds) if not r["spans"]]
    if clean:
        fp_spans = sum(len(p) for _, p in clean)
        report["clean_docs"] = {
            "docs": len(clean),
            "fp_spans_per_1k_docs": round(1000 * fp_spans / len(clean), 1),
            "docs_with_any_fp_pct": round(100 * sum(1 for _, p in clean if p) / len(clean), 2),
            "fp_by_label": dict(Counter(sp["label"] for _, p in clean for sp in p).most_common()),
        }

    # error examples (strict) for the feedback step
    fns, fps = [], []
    for row, p in zip(rows, preds):
        mp, mg = match(row["spans"], p, "strict")
        for gi, g in enumerate(row["spans"]):
            if gi not in mg and len(fns) < args.max_examples:
                fns.append({"id": row["id"], "label": g["label"], "value": row["text"][g["start"]:g["end"]],
                            "context": row["text"][max(0, g["start"] - 40):g["end"] + 40],
                            "predicted_here": [x for x in p if overlaps(x, g)]})
        for pi, x in enumerate(p):
            if pi not in mp and len(fps) < args.max_examples:
                fps.append({"id": row["id"], "label": x["label"], "value": x["value"], "score": x["score"],
                            "context": row["text"][max(0, x["start"] - 40):x["end"] + 40]})
    report["false_negatives"] = fns
    report["false_positives"] = fps

    # table
    print(f"\n{len(rows)} docs, {report['ms_per_doc']} ms/doc on {pred.device}\n")
    print(f"{'label':<15}{'gold':>6} | {'strict P':>8} {'R':>6} {'F1':>6} {'95% CI':>14} | "
          f"{'partial F1':>10} | {'detected R':>10}")
    for n in names:
        s, pa, d = report["strict"][n], report["partial"][n], report["detected"][n]
        ci = f"[{s['f1_ci95'][0]:.2f},{s['f1_ci95'][1]:.2f}]"
        if n == "ALL":
            print("-" * 90)
        print(f"{n:<15}{s['gold_spans']:>6} | {s['precision']:>8.3f} {s['recall']:>6.3f} {s['f1']:>6.3f} "
              f"{ci:>14} | {pa['f1']:>10.3f} | {d['recall']:>10.3f}")

    if "clean_docs" in report:
        c = report["clean_docs"]
        print(f"\nclean docs: {c['docs']} | false positives per 1k docs: {c['fp_spans_per_1k_docs']} | "
              f"docs with any FP: {c['docs_with_any_fp_pct']}% | by label: {c['fp_by_label']}")

    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nreport written to {args.report}")


if __name__ == "__main__":
    main()
