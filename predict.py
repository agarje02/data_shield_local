"""Run the trained detector on text or a file.

Usage:
  python predict.py --model runs/deberta_v0_run1/best --text "Call Rahul on 9876543210"
  python predict.py --model runs/deberta_v0_run1/best --file sample.txt --json
"""
import argparse
import json
import sys
from pathlib import Path

from common import Predictor, mask_text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--text")
    src.add_argument("--file")
    ap.add_argument("--json", action="store_true", help="print spans as JSON only")
    ap.add_argument("--no-thresholds", action="store_true")
    ap.add_argument("--no-validators", action="store_true")
    args = ap.parse_args()

    text = args.text if args.text is not None else Path(args.file).read_text(encoding="utf-8", errors="replace")
    spans = Predictor(args.model).predict([text], use_thresholds=not args.no_thresholds,
                                               use_validators=not args.no_validators)[0]

    if args.json:
        json.dump(spans, sys.stdout, indent=2, ensure_ascii=False)
        print()
        return
    print(f"{len(spans)} sensitive span(s) found")
    for sp in spans:
        print(f"  {sp['label']:<14} {sp['score']:.2f}  {sp['value']!r}  [{sp['start']}:{sp['end']}]")
    print("\nmasked:\n" + mask_text(text, spans))


if __name__ == "__main__":
    main()
