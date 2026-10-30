"""The scan pipeline every client shares.

  text -> model + rules (common.Predictor, cached by text hash)
       -> policy filters (negative_context)
       -> policy assessment (severity, action, combos, document risk)
       -> masked text + audit record

One model instance per process; calls are serialised with a lock, so memory stays bounded
no matter how many clients scan at once.
"""
import copy
import hashlib
import json
import threading
import time
from collections import OrderedDict
from pathlib import Path

from datashield_local.common import Predictor, load_labels, mask_text
from datashield_local.engine.extract import Limits, extract_bytes
from datashield_local.policy import Policy

MAX_CHARS = 200_000
CHUNK_CHARS = 100_000     # file text is scanned in chunks this size, split at line ends


def preview(value):
    """Enough to recognise a finding in a report without printing the secret: 41•••••11."""
    v = value.strip()
    if len(v) <= 4:
        return "•" * len(v)
    return v[:2] + "•" * min(len(v) - 4, 8) + v[-2:]


def chunks(text, size=CHUNK_CHARS):
    """(offset, piece) pieces of at most `size` chars, cut after a newline when possible."""
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            nl = text.rfind("\n", start + size // 2, end)
            end = nl + 1 if nl != -1 else end
        yield start, text[start:end]
        start = end


class Engine:
    def __init__(self, model_dir, policy_path, audit=None, predictor=None, cache_size=256):
        self.model_dir = Path(model_dir)
        # 4 windows per call: same speed on CPU as 16, but a long paste peaks ~500 MB lower
        self.predictor = predictor or Predictor(self.model_dir, batch_size=4)
        self.labels = load_labels(self.model_dir / "labels.yaml")
        manifest = self.model_dir / "manifest.json"
        self.manifest = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
        self.model_id = self.manifest.get("run_id") or self.model_dir.name
        self.policy = Policy(policy_path)
        self.audit = audit
        self.lock = threading.Lock()
        self.cache = OrderedDict()
        self.cache_size = cache_size
        self.started = time.time()
        self.scans = 0

    def detect(self, text, thresholds=True, validators=True):
        """Model + rules spans for one text. Returns (spans, cached)."""
        if not text.strip():
            return [], False
        key = hashlib.sha256(f"{thresholds}{validators}\x00{text}".encode("utf-8")).hexdigest()
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return copy.deepcopy(self.cache[key]), True
            spans = self.predictor.predict([text], use_thresholds=thresholds, use_validators=validators)[0]
            for s in spans:
                s.setdefault("source", "model")
            self.cache[key] = spans
            if len(self.cache) > self.cache_size:
                self.cache.popitem(last=False)
            return copy.deepcopy(spans), False

    def scan(self, text, source="api", context=None, thresholds=True, validators=True, audit=True):
        if len(text) > MAX_CHARS:
            raise ValueError(f"text longer than {MAX_CHARS} characters")
        t0 = time.perf_counter()
        spans, cached = self.detect(text, thresholds, validators)
        spans, dropped = self.policy.filter_spans(text, spans)
        assessment = self.policy.assess(spans)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        self.scans += 1
        # audit: True = always, "findings" = only when something was found (typing checks), False = never
        if self.audit is not None and (audit is True or (audit == "findings" and spans)):
            self.audit.record(text, source, context, assessment, ms)
        return {"spans": spans, "assessment": assessment, "masked": mask_text(text, spans),
                "ms": ms, "chars": len(text), "cached": cached,
                "suppressed": [{"label": s["label"], "start": s["start"], "end": s["end"]} for s in dropped]}

    def scan_document(self, name, data, source="file", context=None, limits=None, include_values=False,
                      audit=True):
        """Scan a file's bytes (any supported format, archives included).

        One assessment for the whole file, so combos work across cells, pages and archive
        members; each finding carries its location (line / page / sheet!cell / member)."""
        t0 = time.perf_counter()
        ex = extract_bytes(name, data, limits)
        found, suppressed = [], 0
        for seg in ex.segments:
            for off, piece in chunks(seg.text):
                spans, _ = self.detect(piece)
                spans, dropped = self.policy.filter_spans(piece, spans)
                suppressed += len(dropped)
                for s in spans:
                    start = off + s["start"]
                    found.append({**s, "start": start, "end": off + s["end"], "loc": seg.locate(start)})
        assessment = self.policy.assess(found)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        self.scans += 1
        if audit and self.audit is not None:
            self.audit.record("", source, {**(context or {}), "file": name}, assessment, ms,
                              chars=sum(len(s.text) for s in ex.segments))
        findings = [{"label": f["label"], "severity": f["severity"], "action": f["action"],
                     "score": f.get("score"), "source": f.get("source"), "location": f["loc"],
                     "preview": preview(f["value"]), **({"value": f["value"]} if include_values else {})}
                    for f in assessment["findings"]]
        return {"name": name, "bytes": len(data), "ms": ms, "risk": assessment["risk"],
                "action": assessment["action"], "counts": assessment["counts"], "combos": assessment["combos"],
                "findings": findings, "suppressed": suppressed, "segments": len(ex.segments),
                "skipped": ex.skipped, "truncated": ex.truncated}

    def scan_path(self, path, **kw):
        path = Path(path)
        size = path.stat().st_size
        if size > (kw.get("limits") or Limits()).max_file_bytes:
            return {"name": str(path), "bytes": size, "risk": "none", "action": "allow", "findings": [],
                    "combos": [], "counts": {}, "skipped": [{"member": str(path), "reason": "file too large"}],
                    "segments": 0, "truncated": False, "ms": 0, "suppressed": 0}
        return self.scan_document(str(path), path.read_bytes(), **kw)

    def info(self):
        p = self.predictor
        return {"model_id": self.model_id, "model_dir": str(self.model_dir), "backend": p.backend,
                "device": p.device, "labels": self.labels, "thresholds": p.thresholds,
                "manifest": self.manifest, "policy": self.policy.to_dict()}
