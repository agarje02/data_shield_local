"""Build a dataset version.

  python -m datagen.build --config datagen/configs/v1.yaml --out data/v1

Writes train / val / test / clean_test JSONL + manifest.json in the same row format the
training code reads: {"id", "text", "spans":[{start,end,label,value}], "template_id", ...}.

Design (see datagen/README.md):
  - most documents are clean or nearly clean (realistic base rate)
  - harmless look-alikes appear in every document type, often with the same wording as PII
  - cues, frames, neutral sentences and record keys are split disjointly across train/val/test
  - clean_test = documents with no PII at all, for the false-positives-per-1k metric
"""
import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

from . import components as C
from .values import Filler, LookalikeValues, SensitiveValues

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from common import load_labels, write_jsonl  # noqa: E402

FILL = re.compile(r"\{(\w+)\}")
SLOT = re.compile(r"\{([A-Z_]+)\}")
SPLITS = ("train", "val", "test")


# ----------------------------------------------------------------------------- splitting

def _split_list(items, rng, frac):
    """Shuffle items and cut into train/val/test; val and test get at least one each when possible."""
    items = list(items)
    rng.shuffle(items)
    n = len(items)
    k = max(1, round(n * frac)) if n >= 3 else 0
    return {"val": items[:k], "test": items[k:2 * k], "train": items[2 * k:]}


def split_components(seed, frac, share_keys=False):
    rng = random.Random(seed)
    out = {s: {} for s in SPLITS}

    def grouped(name, groups):
        for s in SPLITS:
            out[s][name] = {}
        for g, items in sorted(groups.items()):
            parts = _split_list(items, rng, frac)
            for s in SPLITS:
                out[s][name][g] = parts[s] or parts["train"]  # tiny groups fall back to train items

    grouped("pos_cues", C.POS_CUES)
    grouped("neg_cues", C.NEG_CUES)
    grouped("pos_keys", C.POS_KEYS)
    for name, items in (("frames", C.FRAMES), ("neutral", C.NEUTRAL), ("neg_keys", sorted(C.NEG_KEYS)),
                        ("combos", C.COMBOS)):
        parts = _split_list(items, rng, frac)
        for s in SPLITS:
            out[s][name] = parts[s]
    # key contrasts go into every split (components.SHARED)
    shared = getattr(C, "SHARED", {})
    for s in SPLITS:
        for name in ("pos_cues", "neg_cues"):
            for g, items in shared.get(name, {}).items():
                out[s][name][g] = out[s][name][g] + list(items)
        out[s]["combos"] = out[s]["combos"] + list(shared.get("combos", []))
    if share_keys:
        # field names are vocabulary (uid = Aadhaar, security_code = CVV), not phrasing: every
        # split gets all of them, so the test measures unseen wording, not unknown words
        for s in SPLITS:
            out[s]["pos_keys"] = {g: list(v) for g, v in C.POS_KEYS.items()}
            out[s]["neg_keys"] = sorted(C.NEG_KEYS)
    return out


# ----------------------------------------------------------------------------- document builder

class Doc:
    def __init__(self):
        self.parts, self.spans, self.pos = [], [], 0

    def add(self, text):
        self.parts.append(text)
        self.pos += len(text)

    def value(self, val, label=None):
        if label:
            self.spans.append({"start": self.pos, "end": self.pos + len(val), "label": label, "value": val})
        self.add(val)

    @property
    def text(self):
        return "".join(self.parts)


class Composer:
    def __init__(self, comps, labels, seed, cfg):
        self.c, self.labels, self.cfg = comps, labels, cfg
        self.r = random.Random(seed)
        self.sv = SensitiveValues(seed + 1)
        self.sv.prefixless_keys = cfg.get("prefixless_api_keys", False)
        self.lk = LookalikeValues(seed + 2)
        self.fill = Filler(seed + 3, self.lk)
        self.used = Counter()
        # combos whose slots are all labels of this version
        self.combos = [c for c in comps.get("combos", []) if set(SLOT.findall(c)) <= set(labels)]

    # -- pieces ---------------------------------------------------------------
    def pick(self, kind, items):
        it = self.r.choice(items)
        self.used[kind] += 1
        return it

    def cue_sentence(self, doc, positive):
        frame = self.pick("frame", self.c["frames"])
        if positive and self.combos and self.r.random() < self.cfg.get("combo_frac", 0):
            return self.combo_sentence(doc, frame)
        if positive:
            label = self.r.choice(self.labels)
            cue = self.pick("pos_cue", self.c["pos_cues"][label])
            val = getattr(self.sv, label)()
        else:
            label = None
            method = self.r.choice(sorted(self.c["neg_cues"]))
            cue = self.pick("neg_cue", self.c["neg_cues"][method])
            val = getattr(self.lk, method)()
        f_pre, f_post = frame.split("{X}")
        c_pre, c_post = cue.split("{v}")
        if not f_pre and c_pre:
            c_pre = c_pre[0].upper() + c_pre[1:]
        doc.add(f_pre + c_pre)
        doc.value(val, label)
        doc.add(c_post + f_post)

    def combo_sentence(self, doc, frame):
        """A frame around a cue with several {LABEL} slots; every slot becomes a span."""
        combo = self.pick("combo", self.combos)
        f_pre, f_post = frame.split("{X}")
        doc.add(f_pre)
        pos = 0
        for m in SLOT.finditer(combo):
            lead = combo[pos:m.start()]
            if not f_pre and pos == 0 and lead:
                lead = lead[0].upper() + lead[1:]
            doc.add(lead)
            doc.value(getattr(self.sv, m.group(1))(), m.group(1))
            pos = m.end()
        doc.add(combo[pos:] + f_post)

    def neutral_sentence(self, doc):
        tmpl = self.pick("neutral", self.c["neutral"])
        out = FILL.sub(lambda m: self.fill.get(m.group(1)), tmpl)
        doc.add(out)

    def sentence(self, doc, kind):
        if kind == "pos":
            self.cue_sentence(doc, True)
        elif kind == "neg":
            self.cue_sentence(doc, False)
        else:
            self.neutral_sentence(doc)

    def plan(self, n, n_pos):
        """Order of sentence kinds: n_pos PII sentences among neutral and look-alike ones."""
        kinds = ["pos"] * n_pos
        for _ in range(max(0, n - n_pos)):
            kinds.append("neg" if self.r.random() < self.cfg["neg_sentence_frac"] else "neutral")
        self.r.shuffle(kinds)
        return kinds

    def key_style(self, key, fmt):
        """Real exports name the same field many ways: bank_account, BANK_ACCOUNT,
        Bank Account, bankAccount, Bank Account No. Applied to sensitive and look-alike
        keys alike, so the key format never tells the label."""
        keep = self.cfg.get("key_style_keep", 1.0)  # 1.0 (older configs): no draw, same data as before
        if keep >= 1.0 or self.r.random() < keep:
            return key
        words = [w for w in re.split(r"[_\s/]+", key) if w]
        if fmt in ("sql", "env"):
            return "_".join(words).upper() if self.r.random() < 0.5 else "_".join(words).lower()
        style = self.r.choice(["upper_snake", "upper_snake", "title", "title_no", "camel", "lower_space"])
        if style == "upper_snake":
            return "_".join(words).upper()
        if style == "camel":
            return words[0].lower() + "".join(w.capitalize() for w in words[1:])
        if style == "lower_space":
            return " ".join(words).lower()
        title = " ".join(w if w.isupper() else w.capitalize() for w in words)
        return title + (self.r.choice([" No.", " Number", " No", " #"]) if style == "title_no" else "")

    def record(self, doc, n_pos, fmt=None, rows=1):
        """A structured block: key=value, JSON, CSV, YAML, SQL, a Python dict, or one
        line of "key: value" pairs (inline)."""
        fmt = fmt or self.r.choice(["kv", "env", "json", "csv", "yaml", "sql", "pydict"]
                                   + (["inline"] if self.cfg.get("inline_records") else []))
        fields = []
        for label in self.r.sample(self.labels, min(n_pos, len(self.labels))):
            fields.append((self.key_style(self.r.choice(self.c["pos_keys"][label]), fmt), label, None))
        for key in self.r.sample(self.c["neg_keys"], min(len(self.c["neg_keys"]), self.r.randint(1, 3))):
            fields.append((self.key_style(key, fmt), None, C.NEG_KEYS[key]))
        self.r.shuffle(fields)
        self.used["record_" + fmt] += 1

        def val(label, method):
            v = getattr(self.sv, label)() if label else getattr(self.lk, method)()
            # one record = one line per field: multi-line values (PEM keys) are stored
            # escaped, the way .env / JSON configs hold them
            return v.replace("\n", "\\n")

        if fmt == "csv":
            delim = self.r.choice([",", ";", "|", ", ", "\t"])
            doc.add(delim.join(k for k, _, _ in fields) + "\n")
            for ri in range(rows):
                for fi, (_, label, method) in enumerate(fields):
                    if fi:
                        doc.add(delim)
                    doc.value(val(label, method), label)
                doc.add("\n")
            return
        if fmt in ("json", "pydict"):
            q = '"' if fmt == "json" else "'"
            pretty = self.r.random() < 0.5
            doc.add("{" + ("\n  " if pretty else ""))
            for fi, (k, label, method) in enumerate(fields):
                if fi:
                    doc.add(",\n  " if pretty else ", ")
                doc.add(f"{q}{k}{q}: {q}")
                doc.value(val(label, method), label)
                doc.add(q)
            doc.add(("\n" if pretty else "") + "}\n")
            return
        if fmt == "sql":
            doc.add(f"INSERT INTO {self.r.choice(['customers', 'users', 'payouts', 'kyc', 'orders'])} "
                    f"({', '.join(k for k, _, _ in fields)}) VALUES (")
            for fi, (_, label, method) in enumerate(fields):
                if fi:
                    doc.add(", ")
                doc.add("'")
                doc.value(val(label, method), label)
                doc.add("'")
            doc.add(");\n")
            return
        if fmt == "inline":
            delim = self.r.choice([" | ", " || ", "; ", ", ", "  |  "])
            kv = self.r.choice([": ", "=", " - ", ": "])
            for fi, (k, label, method) in enumerate(fields):
                if fi:
                    doc.add(delim)
                doc.add(f"{k}{kv}")
                doc.value(val(label, method), label)
            doc.add("\n")
            return
        sep = {"kv": ": ", "env": "=", "yaml": ": "}[fmt]
        for k, label, method in fields:
            key = k.upper().replace(" ", "_").replace("/", "_") if fmt == "env" else k
            doc.add(f"{key}{sep}")
            v = val(label, method)
            if fmt == "yaml" and self.r.random() < 0.3:
                doc.add('"')
                doc.value(v, label)
                doc.add('"')
            else:
                doc.value(v, label)
            doc.add("\n")

    # -- document styles --------------------------------------------------------
    def person_or(self, doc, fallback, positive):
        if positive:
            doc.value(self.sv.PERSON(), "PERSON")
        else:
            doc.add(fallback)

    def paragraph(self, doc, kinds):
        for i, k in enumerate(kinds):
            if i:
                doc.add(" ")
            self.sentence(doc, k)

    def style_email(self, doc, kinds, positive):
        doc.add("Hi ")
        self.person_or(doc, self.r.choice(["team", "all", "there", "everyone"]), positive and self.r.random() < 0.5)
        doc.add(",\n\n")
        chunks = [kinds[i:i + self.r.randint(2, 4)] for i in range(0, len(kinds), 3)]
        for ci, ch in enumerate(chunks):
            if ci:
                doc.add("\n\n")
            self.paragraph(doc, ch)
        if self.r.random() < 0.25:
            doc.add("\n\n")
            self.record(doc, self.r.randint(1, 2) if positive else 0, fmt="kv")
        doc.add("\n\n" + self.r.choice(["Regards", "Thanks", "Best", "Cheers"]) + ",\n")
        self.person_or(doc, f"{self.fill.get('team')} team", positive and self.r.random() < 0.6)

    def style_chat(self, doc, kinds, positive):
        if positive:
            speakers = [self.sv.PERSON(), self.sv.PERSON()]
        else:
            speakers = self.r.sample(["Agent", "Customer", "User", "Bot", "Support", "Me"], 2)
        for i, k in enumerate(kinds):
            if i:
                doc.add("\n")
            doc.add(f"[{self.fill.get('time')}] ")
            doc.value(speakers[i % 2], "PERSON" if positive else None)
            doc.add(": ")
            self.sentence(doc, k)

    def style_log(self, doc, kinds, positive):
        for i, k in enumerate(kinds):
            if i:
                doc.add("\n")
            d = self.lk.a_date()
            doc.add(f"{d.isoformat()}T{self.fill.get('time')}:{self.r.randint(10, 59)}Z "
                    f"{self.fill.get('level')} {self.fill.get('service')}: ")
            self.sentence(doc, k)

    def style_ticket(self, doc, kinds, positive):
        doc.add(f"Ticket {self.lk.ticket_no()} [{self.r.choice(['P1', 'P2', 'P3'])}]\n\n")
        self.paragraph(doc, kinds)

    def style_memo(self, doc, kinds, positive):
        chunks = [kinds[i:i + 4] for i in range(0, len(kinds), 4)]
        for ci, ch in enumerate(chunks):
            if ci:
                doc.add("\n\n")
            self.paragraph(doc, ch)

    def style_config(self, doc, kinds, positive):
        doc.add(f"# {self.fill.get('service')} config for {self.fill.get('env')}\n")
        self.record(doc, kinds.count("pos"), fmt=self.r.choice(["env", "yaml", "json"]))

    def style_form(self, doc, kinds, positive):
        self.record(doc, max(kinds.count("pos"), 0), fmt="kv")

    def style_table(self, doc, kinds, positive):
        self.record(doc, min(kinds.count("pos"), 4), fmt="csv", rows=self.r.randint(1, 4))

    def style_mixed(self, doc, kinds, positive):
        """Prose, then a structured block, then more prose."""
        half = len(kinds) // 2
        self.paragraph(doc, kinds[:half] or ["neutral"])
        doc.add("\n\n")
        self.record(doc, self.r.randint(0, 2) if positive else 0)
        doc.add("\n")
        self.paragraph(doc, kinds[half:] or ["neutral"])

    STYLES = ("email", "chat", "log", "ticket", "memo", "config", "form", "table", "mixed")

    def document(self, positive):
        cfg = self.r
        long = cfg.random() < self.cfg["long_doc_frac"]
        n = cfg.randint(18, 40) if long else cfg.randint(2, 9)
        n_pos = cfg.randint(1, 3 if not long else 6) if positive else 0
        style = cfg.choices(self.STYLES, weights=[self.cfg["style_weights"][s] for s in self.STYLES])[0]
        doc = Doc()
        getattr(self, "style_" + style)(doc, self.plan(n, n_pos), positive)
        return doc, style


# ----------------------------------------------------------------------------- main

def build_split(name, comps, labels, n, seed, cfg, clean_only=False, exclude=None):
    """exclude: texts already used (train); an eval doc identical to one is generated again."""
    comp = Composer(comps, labels, seed, cfg)
    rows = []
    for i in range(n):
        positive = False if clean_only else comp.r.random() >= cfg["clean_doc_frac"]
        doc, style = comp.document(positive)
        while exclude and doc.text in exclude:
            doc, style = comp.document(positive)
        rows.append({"id": f"{cfg['version']}-{name}-{i:05d}", "text": doc.text, "spans": doc.spans,
                     "template_id": style, "doc_type": "pii" if doc.spans else "clean"})
    return rows, comp.used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(Path(__file__).parent / "configs" / "v1.yaml"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--labels", default=str(ROOT / "configs" / "labels.yaml"))
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    labels = load_labels(args.labels)
    missing = [l for l in labels if l not in C.POS_CUES or l not in C.POS_KEYS]
    if missing:
        sys.exit(f"labels without cues/keys in components.py: {missing}")

    splits = split_components(cfg["seed"], cfg["eval_frac"], cfg.get("share_keys", False))
    out = Path(args.out)
    manifest = {"version": cfg["version"], "generator": "datagen", "config": cfg, "labels": labels, "files": {}}
    jobs = [(s, s, cfg["sizes"][s], False) for s in SPLITS] + [("clean_test", "test", cfg["sizes"]["clean_test"], True)]
    seen = set()  # train texts: no eval doc may repeat one
    for i, (fname, comp_split, n, clean_only) in enumerate(jobs):
        rows, used = build_split(fname, splits[comp_split], labels, n, cfg["seed"] * 1000 + i * 17, cfg, clean_only,
                                 exclude=seen if fname != "train" else None)
        if fname == "train":
            seen = {r["text"] for r in rows}
        path = out / f"{fname}.jsonl"
        write_jsonl(path, rows)
        counts = Counter(s["label"] for r in rows for s in r["spans"])
        manifest["files"][fname] = {
            "rows": len(rows), "clean_rows": sum(1 for r in rows if not r["spans"]),
            "styles": dict(Counter(r["template_id"] for r in rows)),
            "span_counts": dict(sorted(counts.items())),
            "avg_chars": round(sum(len(r["text"]) for r in rows) / max(1, len(rows))),
            "components_used": dict(sorted(used.items())),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        print(f"{fname:<11} {len(rows):>6} rows | {sum(counts.values()):>6} spans | "
              f"clean {manifest['files'][fname]['clean_rows']:>5} | avg {manifest['files'][fname]['avg_chars']} chars")
    manifest["component_split"] = {s: {k: (sum(len(x) for x in v.values()) if isinstance(v, dict) else len(v))
                                       for k, v in splits[s].items()} for s in SPLITS}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {out}/manifest.json")


if __name__ == "__main__":
    main()
