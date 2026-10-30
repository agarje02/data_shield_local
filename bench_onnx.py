"""Memory and speed of ONNX model variants on this machine.

Each model runs in its own fresh process (so one model's memory can't hide
another's), scanning test docs one at a time like the local app does, then one
long document. Reports resident memory after loading and the peak.

Usage:
  python bench_onnx.py models/deberta_v1_onnx models/deberta_v1_onnx_int8 models/deberta_v1_onnx_fp16
"""
import argparse
import json
import subprocess
import sys
import time


def _mb(n):
    return round(n / 2**20)


def child(model, data, docs, batch):
    import os

    import psutil

    from common import Predictor, read_jsonl

    proc = psutil.Process(os.getpid())
    base = proc.memory_info().rss
    t = time.perf_counter()
    pred = Predictor(model, batch_size=batch)
    load_s = time.perf_counter() - t
    loaded = proc.memory_info().rss
    load_peak = getattr(proc.memory_info(), "peak_wset", loaded)
    rows = read_jsonl(data)[:docs]
    t = time.perf_counter()
    for r in rows:
        pred.predict([r["text"]])
    ms_doc = (time.perf_counter() - t) * 1000 / len(rows)
    long_text = "\n\n".join(r["text"] for r in rows)
    t = time.perf_counter()
    pred.predict([long_text])
    long_s = time.perf_counter() - t
    mi = proc.memory_info()
    end = mi.rss
    # peak_wset is Windows-only; elsewhere the current RSS is the best we have
    peak = getattr(mi, "peak_wset", mi.rss)
    print(json.dumps({
        "model": model, "load_s": round(load_s, 1), "rss_after_load_mb": _mb(loaded),
        "model_mem_mb": _mb(loaded - base), "load_peak_mb": _mb(load_peak), "peak_mb": _mb(peak),
        "rss_end_mb": _mb(end),
        "ms_per_doc": round(ms_doc), "long_doc_chars": len(long_text), "long_doc_s": round(long_s, 1),
    }))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--data", default="data/v1/test.jsonl")
    ap.add_argument("--docs", type=int, default=50)
    ap.add_argument("--batch", type=int, default=16, help="windows per model call")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.child:
        return child(args.models[0], args.data, args.docs, args.batch)

    results = []
    for m in args.models:
        out = subprocess.run([sys.executable, __file__, m, "--child", "--data", args.data,
                              "--docs", str(args.docs), "--batch", str(args.batch)], capture_output=True, text=True)
        if out.returncode:
            print(f"{m} failed:\n{out.stderr[-2000:]}")
            continue
        results.append(json.loads(out.stdout.strip().splitlines()[-1]))
        print(json.dumps(results[-1]), flush=True)

    print(f"\n{'model':<34}{'load s':>7}{'RAM loaded':>12}{'load peak':>11}{'scan peak':>11}{'RAM end':>9}"
          f"{'ms/doc':>8}{'long doc s':>11}")
    for r in results:
        print(f"{r['model']:<34}{r['load_s']:>7}{r['rss_after_load_mb']:>9} MB{r['load_peak_mb']:>8} MB"
              f"{r['peak_mb']:>8} MB{r['rss_end_mb']:>6} MB"
              f"{r['ms_per_doc']:>8}{r['long_doc_s']:>11}")


if __name__ == "__main__":
    main()
