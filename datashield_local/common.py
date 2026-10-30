"""Shared helpers for training, evaluation and prediction.

Everything that turns character spans into token labels (and back) lives here,
so train / evaluate / predict can never drift apart.
"""
import json
import re
from pathlib import Path

import yaml

from datashield_local import validators

# Trailing characters a sub-word token may drag into a predicted span.
_TRIM_TRAILING = ".,;:!?)]}\"'|=>"
_TRIM_LEADING = "([{\"';,|=:<"
# Labels whose values may legitimately end with punctuation.
# (API keys are only [A-Za-z0-9_-], so trailing sentence punctuation is always trimmed for them.)
_NO_PUNCT_TRIM = {"PASSWORD"}
# Labels whose values may legitimately start with an opening bracket, e.g. "(+91) 98765 43210".
_KEEP_LEADING_BRACKET = {"PHONE"}
# Characters that separate "words" for word-level label voting (besides whitespace).
_WORD_DELIMS = set(";,|\"'=:<>[]{}")


# ---------------------------------------------------------------- labels

def load_labels(path):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return list(cfg["labels"])


def build_label_maps(labels):
    """BIO tag maps: O, B-X, I-X for every entity type X."""
    tags = ["O"]
    for lab in labels:
        tags += [f"B-{lab}", f"I-{lab}"]
    label2id = {t: i for i, t in enumerate(tags)}
    id2label = {i: t for t, i in label2id.items()}
    return label2id, id2label


# ---------------------------------------------------------------- io

def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- spans -> token labels

def spans_to_token_labels(offsets, sequence_ids, spans, label2id):
    """Label one tokenized window.

    offsets: list of (start, end) char offsets per token.
    sequence_ids: per-token sequence id (None for special tokens).
    spans: list of {"start", "end", "label"} in char space.
    Returns a list of label ids, -100 for special tokens.
    """
    spans = sorted(spans, key=lambda s: s["start"])
    labels = []
    for (s, e), seq in zip(offsets, sequence_ids):
        if seq is None or s == e:
            labels.append(-100)
            continue
        tag = "O"
        for sp in spans:
            if s < sp["end"] and e > sp["start"]:
                # first token touching the span start is B, the rest I
                prefix = "B" if s <= sp["start"] else "I"
                tag = f"{prefix}-{sp['label']}"
                break
            if sp["start"] >= e:
                break
        labels.append(label2id[tag])
    return labels


# ---------------------------------------------------------------- token predictions -> spans

def _trim(text, start, end, label):
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if label not in _NO_PUNCT_TRIM:
        while end > start and text[end - 1] in _TRIM_TRAILING:
            end -= 1
        leading = _TRIM_LEADING.replace("(", "") if label in _KEEP_LEADING_BRACKET else _TRIM_LEADING
        while start < end and text[start] in leading:
            start += 1
    else:
        if end > start + 1 and text[end - 1] in ".,;?" and (end == len(text) or text[end].isspace()):
            # passwords keep their symbols, but not the punctuation that ends the sentence
            # ("!" stays: Summer2024! is a common password, "?" at the end of one is rare)
            end -= 1
        while end > start + 1 and text[start] in ":=":
            # the separator of "user:pass" / "pwd=..." the token model pulled in
            start += 1
    return start, end


_YEAR_AFTER = re.compile(r",?\s?(?:19|20)\d{2}\b")
_HAS_YEAR = re.compile(r"(?:19|20)\d{2}")


_USER_PASS = re.compile(r"([A-Za-z][\w.\-]{2,63}):(\S+)")


def repair_boundaries(text, spans, labels=()):
    """Format-aware fixes the token model gets wrong at span edges.

    DATE_OF_BIRTH: 'May 01' followed by ', 1984' -> extend over the year.
    PASSWORD 'rahul92:Secret@1' -> USERNAME 'rahul92' + PASSWORD 'Secret@1'
             (only for models that have a USERNAME label).
    """
    out = []
    for sp in spans:
        if sp["label"] == "DATE_OF_BIRTH" and not _HAS_YEAR.search(sp["value"]):
            m = _YEAR_AFTER.match(text, sp["end"])
            if m:
                sp["end"] = m.end()
                sp["value"] = text[sp["start"]:sp["end"]]
        m = _USER_PASS.fullmatch(sp["value"]) if sp["label"] == "PASSWORD" and "USERNAME" in labels else None
        if m:
            s = sp["start"]
            out.append({**sp, "end": s + m.end(1), "label": "USERNAME", "value": m.group(1)})
            out.append({**sp, "start": s + m.start(2), "value": m.group(2)})
            continue
        out.append(sp)
    return out


def tokens_to_spans(text, tokens, id2label):
    """tokens: list of (start, end, label_id, prob) sorted by start.

    Groups B-/I- runs into character spans with a mean confidence score.
    """
    spans = []
    cur = None

    def close():
        if cur is None:
            return
        s, e = _trim(text, cur["start"], cur["end"], cur["label"])
        if s < e:
            spans.append({
                "start": s, "end": e, "label": cur["label"],
                "value": text[s:e],
                "score": round(sum(cur["probs"]) / len(cur["probs"]), 4),
            })

    for s, e, lid, prob in tokens:
        tag = id2label[lid]
        if tag == "O":
            close()
            cur = None
            continue
        prefix, lab = tag.split("-", 1)
        # token offsets may include leading whitespace, so look up to the first visible char
        lead = s
        while lead < e and text[lead].isspace():
            lead += 1
        if cur is not None and prefix == "I" and lab == cur["label"] and "\n" not in text[cur["end"]:lead]:
            cur["end"] = max(cur["end"], e)
            cur["probs"].append(prob)
        else:
            close()
            cur = {"start": s, "end": e, "label": lab, "probs": [prob]}
    close()
    return spans


def word_ids(text, tokens):
    """Word index per token. A word is a run of characters without whitespace or
    _WORD_DELIMS; a delimiter token forms its own word."""
    starts = []
    wid, prev_end = -1, None
    for s, e in tokens:
        first = s
        while first < e and text[first].isspace():
            first += 1
        piece = text[first:e]
        gap = text[prev_end:first] if prev_end is not None else " "
        new_word = (prev_end is None or gap != "" or (piece and piece[0] in _WORD_DELIMS)
                    or (prev_end > 0 and text[prev_end - 1] in _WORD_DELIMS))
        if new_word:
            wid += 1
        starts.append(wid)
        prev_end = e
    return starts


def aggregate_words(text, tokens, id2label):
    """Give every sub-word token of a word the same label.

    tokens: list of (start, end, prob_vector) sorted by start.
    The entity type is chosen by summing B-X + I-X probability over the word's
    tokens; the B/I prefix comes from the word's first token. This stops a number
    like "897|4 8947 9629" from being split across two labels.
    Punctuation-only tokens do not vote (they still get the word's label): in
    "cvv 765." the "." is trained as O and would otherwise cancel a one-token value.
    Returns (start, end, label_id, prob) per word, ready for tokens_to_spans.
    """
    label2id = {v: k for k, v in id2label.items()}
    types = sorted({t.split("-", 1)[1] for t in id2label.values() if t != "O"})
    o_id = label2id["O"]
    ids = word_ids(text, [(s, e) for s, e, _ in tokens])
    out, i = [], 0
    while i < len(tokens):
        j = i
        while j + 1 < len(tokens) and ids[j + 1] == ids[i]:
            j += 1
        group = tokens[i:j + 1]
        voters = [g for g in group if any(c.isalnum() for c in text[g[0]:g[1]])] or group
        n = len(voters)
        scores = {"O": sum(p[o_id] for _, _, p in voters) / n}
        for t in types:
            scores[t] = sum(p[label2id[f"B-{t}"]] + p[label2id[f"I-{t}"]] for _, _, p in voters) / n
        best = max(scores, key=scores.get)
        if best == "O":
            lid = o_id
        else:
            first = group[0][2]
            prefix = "B" if first[label2id[f"B-{best}"]] >= first[label2id[f"I-{best}"]] else "I"
            lid = label2id[f"{prefix}-{best}"]
        out.append((group[0][0], group[-1][1], lid, scores[best]))
        i = j + 1
    return out


def apply_thresholds(spans, thresholds):
    if not thresholds:
        return spans
    default = thresholds.get("default", 0.0)
    return [sp for sp in spans if sp["score"] >= thresholds.get(sp["label"], default)]


def mask_text(text, spans):
    out, last = [], 0
    for sp in sorted(spans, key=lambda s: s["start"]):
        if sp["start"] < last:
            continue
        out.append(text[last:sp["start"]])
        out.append(f"[{sp['label']}]")
        last = sp["end"]
    out.append(text[last:])
    return "".join(out)


# ---------------------------------------------------------------- bio sequences (token space)

def bio_entities(tags):
    """Entities from a BIO tag-string sequence as (label, start_idx, end_idx).

    An I- tag without a preceding matching tag starts a new entity (lenient,
    same as tokens_to_spans).
    """
    ents, cur = [], None
    for i, tag in enumerate(list(tags) + ["O"]):
        if cur and tag.startswith("I-") and tag[2:] == cur[0]:
            continue
        if cur:
            ents.append((cur[0], cur[1], i))
            cur = None
        if tag != "O":
            cur = (tag[2:], i)
    return ents


# ---------------------------------------------------------------- inference

class Predictor:
    """Sliding-window token-classification inference with span merging.

    backend: "torch", "onnx" (needs model.onnx in model_dir, see export_onnx.py)
    or "auto" (onnx when model.onnx exists). Decoding is identical for both.
    """

    def __init__(self, model_dir, device=None, max_length=512, stride=128, batch_size=16, backend="auto"):
        from transformers import AutoTokenizer

        model_dir = Path(model_dir)
        if backend == "auto":
            backend = "onnx" if (model_dir / "model.onnx").exists() else "torch"
        self.backend = backend
        self.tokenizer = AutoTokenizer.from_pretrained(model_dir)
        if backend == "onnx":
            import onnxruntime as ort
            providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider")
                         if p in ort.get_available_providers()]
            opts = ort.SessionOptions()
            # without this the CPU arena keeps the buffers of the largest batch ever
            # run (~1 GB after a long document) for the life of the process
            opts.enable_cpu_mem_arena = False
            self.session = ort.InferenceSession(str(model_dir / "model.onnx"), opts, providers=providers)
            self.device = "onnx-" + self.session.get_providers()[0].replace("ExecutionProvider", "").lower()
            config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
            self.id2label = {int(k): v for k, v in config["id2label"].items()}
        else:
            import torch
            import transformers
            from transformers import AutoModelForTokenClassification

            self.torch = torch
            self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
            dtype_key = "dtype" if int(transformers.__version__.split(".")[0]) >= 5 else "torch_dtype"
            self.model = AutoModelForTokenClassification.from_pretrained(
                model_dir, **{dtype_key: torch.float32}).to(self.device).eval()
            self.id2label = {int(k): v for k, v in self.model.config.id2label.items()}
        self.label_types = {t.split("-", 1)[1] for t in self.id2label.values() if t != "O"}
        self.max_length = max_length
        self.stride = stride
        self.batch_size = batch_size
        thr = model_dir / "thresholds.json"
        self.thresholds = json.loads(thr.read_text()) if thr.exists() else {}

    def _probs(self, input_ids, attention_mask):
        """Softmax probabilities, shape (windows, tokens, tags), as nested lists."""
        if self.backend == "onnx":
            import numpy as np
            logits = self.session.run(["logits"], {"input_ids": input_ids.astype(np.int64),
                                                   "attention_mask": attention_mask.astype(np.int64)})[0]
            logits = logits - logits.max(axis=-1, keepdims=True)
            e = np.exp(logits)
            return (e / e.sum(axis=-1, keepdims=True)).tolist()
        torch = self.torch
        with torch.no_grad():
            logits = self.model(input_ids=torch.as_tensor(input_ids).to(self.device),
                                attention_mask=torch.as_tensor(attention_mask).to(self.device)).logits
        return torch.softmax(logits.float(), dim=-1).cpu().tolist()

    def predict(self, texts, use_thresholds=True, use_validators=True):
        """Returns a list (one per text) of span dicts.

        use_validators: drop spans that fail format/checksum rules and add
        rule-only secret matches (see validators.py)."""
        enc = self.tokenizer(
            texts, truncation=True, max_length=self.max_length, stride=self.stride,
            return_overflowing_tokens=True, return_offsets_mapping=True, padding=True,
            return_tensors="np",
        )
        sample_map = enc["overflow_to_sample_mapping"].tolist()
        offsets = enc["offset_mapping"].tolist()
        n_win = len(sample_map)

        # best prediction per (text, token offset): keep the window where the
        # token sits furthest from a window edge
        best = [dict() for _ in texts]
        for b0 in range(0, n_win, self.batch_size):
            b1 = min(b0 + self.batch_size, n_win)
            probs = self._probs(enc["input_ids"][b0:b1], enc["attention_mask"][b0:b1])
            for w in range(b0, b1):
                seq_ids = enc.sequence_ids(w)
                content = [i for i, sid in enumerate(seq_ids) if sid is not None]
                if not content:
                    continue
                first, last = content[0], content[-1]
                for i in content:
                    s, e = offsets[w][i]
                    if s == e:
                        continue
                    centrality = min(i - first, last - i)
                    key = (s, e)
                    prev = best[sample_map[w]].get(key)
                    if prev is None or centrality > prev[0]:
                        best[sample_map[w]][key] = (centrality, probs[w - b0][i])

        results = []
        for ti, text in enumerate(texts):
            toks = sorted(((s, e, p) for (s, e), (_, p) in best[ti].items()), key=lambda t: (t[0], t[1]))
            spans = repair_boundaries(text, tokens_to_spans(text, aggregate_words(text, toks, self.id2label), self.id2label),
                                      self.label_types)
            if use_validators:
                spans = validators.apply(text, spans)
            if use_thresholds:
                spans = apply_thresholds(spans, self.thresholds)
            results.append(spans)
        return results
