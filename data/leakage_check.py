"""Leakage check: evaluation text must not appear in training data.

Checks val/test (and any extra eval files) against train for
  1. exact duplicate texts
  2. sensitive values shared with train (the model could just memorise them)
  3. word 8-gram overlap: share of an eval doc's 8-grams that also occur in train

Usage:
  python data/leakage_check.py data/v0
  python data/leakage_check.py data/v0 --extra golden/golden_test.jsonl
Exit code 1 when leakage exceeds the thresholds.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import read_jsonl  # noqa: E402

WORD = re.compile(r"\w+")


def ngrams(text, n):
    words = WORD.findall(text.lower())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("--extra", nargs="*", default=[], help="more eval JSONL files to check against train")
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--doc-overlap", type=float, default=0.5,
                    help="a doc counts as leaked if more than this share of its n-grams occur in train")
    ap.add_argument("--max-leaked-frac", type=float, default=0.01,
                    help="fail if more than this share of eval docs are leaked")
    ap.add_argument("--max-shared-values", type=int, default=-1,
                    help="fail if more than this many sensitive values are shared with train "
                         "(-1 = report only; common first names / weak passwords collide by chance)")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    train = read_jsonl(data_dir / "train.jsonl")
    train_texts = {r["text"] for r in train}
    train_values = {sp["value"] for r in train for sp in r["spans"] if "value" in sp}
    train_grams = set()
    for r in train:
        train_grams |= ngrams(r["text"], args.n)

    evals = {s: data_dir / f"{s}.jsonl" for s in ("val", "test")}
    evals.update({Path(p).stem: Path(p) for p in args.extra})

    failed = False
    for name, path in evals.items():
        rows = read_jsonl(path)
        exact = sum(1 for r in rows if r["text"] in train_texts)
        shared = sorted({sp["value"] for r in rows for sp in r["spans"]
                         if sp.get("value") in train_values})
        leaked = 0
        for r in rows:
            g = ngrams(r["text"], args.n)
            if g and len(g & train_grams) / len(g) > args.doc_overlap:
                leaked += 1
        frac = leaked / max(1, len(rows))
        print(f"{name}: {len(rows)} docs | exact dup={exact} | shared values={len(shared)} | "
              f"{args.n}-gram leaked docs={leaked} ({frac:.1%})")
        if shared:
            print(f"   shared values sample: {shared[:5]}")
        too_many_shared = args.max_shared_values >= 0 and len(shared) > args.max_shared_values
        if exact or frac > args.max_leaked_frac or too_many_shared:
            failed = True
            print("   <-- FAIL")

    print("\nLEAKAGE CHECK FAILED" if failed else "\nleakage check passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
