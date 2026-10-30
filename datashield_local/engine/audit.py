"""Audit log: what was found, where and what the policy did. Never the values.

One JSON line per scan with findings (label, severity, action, position, salted hash of the
value). The hash lets you tell "the same card number again" without storing it; the salt is
per device, so hashes cannot be matched across devices or brute-forced from a public list.
Rotates at max_bytes, keeping `backups` old files.
"""
import hashlib
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

CONTEXT_KEYS = {"app", "host", "file", "event"}


def clean_context(context):
    """Keep only coarse context: a URL is reduced to its host, unknown keys are dropped."""
    out = {}
    for k, v in (context or {}).items():
        if k == "url" and isinstance(v, str):
            out["host"] = urlparse(v).hostname or ""
        elif k in CONTEXT_KEYS and isinstance(v, (str, int, float)):
            out[k] = str(v)[:200]
    return out


class AuditLog:
    def __init__(self, path, salt, max_bytes=10_000_000, backups=5):
        self.path = Path(path)
        self.salt = salt.encode()
        self.max_bytes = max_bytes
        self.backups = backups
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def value_hash(self, value):
        return hashlib.sha256(self.salt + value.encode("utf-8")).hexdigest()[:16]

    def record(self, text, source, context, assessment, ms, chars=None):
        """text: the scanned text (values are hashed from it); for files the findings carry
        "value" and "loc" themselves and text is ""."""
        findings = []
        for f in assessment["findings"]:
            value = f["value"] if "value" in f else text[f["start"]:f["end"]]
            item = {"label": f["label"], "severity": f["severity"], "action": f["action"],
                    "start": f["start"], "end": f["end"], "h": self.value_hash(value)}
            if "loc" in f:
                item["loc"] = f["loc"]
            findings.append(item)
        event = {"ts": round(time.time(), 3), "source": source, "context": clean_context(context),
                 "chars": len(text) if chars is None else chars, "ms": ms, "risk": assessment["risk"],
                 "action": assessment["action"],
                 "combos": [c["name"] for c in assessment["combos"]], "findings": findings}
        line = json.dumps(event, ensure_ascii=False) + "\n"
        with self.lock:
            self._rotate_if_needed(len(line))
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)

    def _rotate_if_needed(self, incoming):
        if not self.path.exists() or self.path.stat().st_size + incoming <= self.max_bytes:
            return
        for i in range(self.backups - 1, 0, -1):
            older = self.path.with_suffix(f".jsonl.{i}")
            if older.exists():
                older.replace(self.path.with_suffix(f".jsonl.{i + 1}"))
        self.path.replace(self.path.with_suffix(".jsonl.1"))

    def tail(self, n=50):
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(x) for x in reversed(lines)]
