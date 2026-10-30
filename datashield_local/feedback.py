"""Local feedback store (SQLite).

Every reviewed scan is saved with what the model predicted and what the
reviewer says is correct. The diff between the two is the training signal:

  missed     - reviewer added a span the model did not find   (false negative)
  wrong      - model span the reviewer removed                 (false positive)
  relabel    - same text, different label
  boundary   - same label, overlapping but different edges

Export writes the corrected docs in the training JSONL format, so they can be
used as a real-world test set or mixed into training.

The text you review is stored as-is (it is your own local data and may contain
real PII), so the database lives only on this machine; never commit it.

CLI:
  python -m app.feedback export feedback/export.jsonl
  python -m app.feedback export feedback/golden.jsonl --golden
  python -m app.feedback stats

--golden drops gold spans whose value cannot be its label (an Aadhaar failing
Verhoeff, a placeholder key, ...): typed test data is often made up, and the
runtime rules reject such values by design, so they would count as misses.
"""
import argparse
import hashlib
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

from datashield_local.engine.paths import default_feedback_db

DEFAULT_DB = default_feedback_db()

SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  REAL NOT NULL,
    text        TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    predicted   TEXT NOT NULL,   -- JSON list of spans from the engine
    corrected   TEXT NOT NULL,   -- JSON list of spans after review
    diff        TEXT NOT NULL,   -- JSON {missed, wrong, relabel, boundary, correct}
    note        TEXT,
    model_id    TEXT,
    source      TEXT             -- where the text came from: typed, file:<name>, sample
);
"""


def _overlap(a, b):
    return a["start"] < b["end"] and b["start"] < a["end"]


def diff_spans(predicted, corrected):
    """Classify every predicted / corrected span against the other list."""
    out = {"correct": [], "missed": [], "wrong": [], "relabel": [], "boundary": []}
    used = set()
    for c in corrected:
        exact = [i for i, p in enumerate(predicted)
                 if i not in used and (p["start"], p["end"], p["label"]) == (c["start"], c["end"], c["label"])]
        if exact:
            used.add(exact[0])
            out["correct"].append(c["label"])
            continue
        near = [i for i, p in enumerate(predicted) if i not in used and _overlap(p, c)]
        if not near:
            out["missed"].append(c["label"])
            continue
        p = predicted[near[0]]
        used.add(near[0])
        if p["label"] != c["label"]:
            out["relabel"].append(f"{p['label']}->{c['label']}")
        else:
            out["boundary"].append(c["label"])
    out["wrong"] = [p["label"] for i, p in enumerate(predicted) if i not in used]
    return out


def _clean(spans, text):
    keep = ("start", "end", "label")
    res = []
    for s in sorted(spans, key=lambda s: s["start"]):
        s = {k: int(s[k]) if k != "label" else str(s[k]) for k in keep}
        if not (0 <= s["start"] < s["end"] <= len(text)):
            raise ValueError(f"span out of range: {s}")
        s["value"] = text[s["start"]:s["end"]]
        res.append(s)
    return res


class FeedbackStore:
    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def add(self, text, predicted, corrected, note="", model_id="", source="typed"):
        predicted = [{"start": int(p["start"]), "end": int(p["end"]), "label": p["label"],
                      "value": text[int(p["start"]):int(p["end"])], "score": p.get("score"),
                      "source": p.get("source", "model")} for p in predicted]
        corrected = _clean(corrected, text)
        d = diff_spans(predicted, corrected)
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO feedback (created_at, text, text_sha256, predicted, corrected, diff, note, model_id, source)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (time.time(), text, hashlib.sha256(text.encode()).hexdigest(), json.dumps(predicted),
                 json.dumps(corrected), json.dumps(d), note, model_id, source))
            return cur.lastrowid, d

    def delete(self, fid):
        with self._conn() as c:
            return c.execute("DELETE FROM feedback WHERE id = ?", (fid,)).rowcount

    def rows(self, limit=None):
        q = "SELECT * FROM feedback ORDER BY id DESC" + (f" LIMIT {int(limit)}" if limit else "")
        with self._conn() as c:
            return [dict(r) for r in c.execute(q)]

    def recent(self, limit=50):
        out = []
        for r in self.rows(limit):
            d = json.loads(r["diff"])
            out.append({"id": r["id"], "created_at": r["created_at"], "source": r["source"], "note": r["note"],
                        "model_id": r["model_id"], "preview": r["text"][:140],
                        "counts": {k: len(v) for k, v in d.items()}})
        return out

    def stats(self):
        totals, by_label = Counter(), {k: Counter() for k in ("missed", "wrong", "relabel", "boundary", "correct")}
        rows = self.rows()
        for r in rows:
            for k, v in json.loads(r["diff"]).items():
                totals[k] += len(v)
                by_label[k].update(v)
        found = totals["correct"] + totals["boundary"] + totals["relabel"]
        return {"docs": len(rows),
                "clean_docs": sum(1 for r in rows if not json.loads(r["corrected"])),
                "totals": dict(totals),
                "precision": round(totals["correct"] / max(1, found + totals["wrong"]), 4),
                "recall": round(totals["correct"] / max(1, found + totals["missed"]), 4),
                "by_label": {k: dict(v.most_common()) for k, v in by_label.items()}}

    def export_jsonl(self, golden=False, dropped=None):
        """Corrected docs in the training format (oldest first).

        golden: drop spans failing validators.is_valid; counts per label go into
        the `dropped` Counter when one is passed."""
        if golden:
            from datashield_local import validators
        lines = []
        for r in reversed(self.rows()):
            spans = json.loads(r["corrected"])
            if golden:
                bad = [sp for sp in spans if not validators.is_valid(sp["label"], r["text"][sp["start"]:sp["end"]])]
                if dropped is not None:
                    dropped.update(sp["label"] for sp in bad)
                spans = [sp for sp in spans if sp not in bad]
            lines.append(json.dumps({"id": f"fb-{r['id']}", "text": r["text"], "spans": spans,
                                     "template_id": "feedback", "source": r["source"]}, ensure_ascii=False))
        return "\n".join(lines) + ("\n" if lines else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["export", "stats"])
    ap.add_argument("out", nargs="?")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--golden", action="store_true", help="drop gold spans that fail the format/checksum rules")
    args = ap.parse_args()
    store = FeedbackStore(args.db)
    if args.cmd == "stats":
        print(json.dumps(store.stats(), indent=2))
        return
    dropped = Counter()
    data = store.export_jsonl(golden=args.golden, dropped=dropped)
    if dropped:
        print(f"dropped spans failing the rules: {dict(dropped)}", file=sys.stderr)
    if args.out:
        Path(args.out).write_text(data, encoding="utf-8")
        print(f"wrote {data.count(chr(10))} docs to {args.out}")
    else:
        sys.stdout.write(data)


if __name__ == "__main__":
    main()
