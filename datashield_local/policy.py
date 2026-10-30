"""Policy layer: severity, action and document risk for detected spans.

The model and validators decide *what* a value is; this decides *how much it matters*,
from configs/policy.yaml (editable without retraining):

  severity per label   CVV critical, AADHAAR high, PHONE medium, PERSON low, ...
  action per level     block / mask / warn / allow
  combos               values that together are worse than alone (card + CVV, name + Aadhaar + DOB)
  document risk        the highest level among findings and fired combos
  negative_context     phrases that make a short value harmless ("best before 09/27")
"""
import re
from pathlib import Path

import yaml

DEFAULT_POLICY = Path(__file__).resolve().parent / "policy.yaml"


class Policy:
    def __init__(self, path=DEFAULT_POLICY):
        cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        self.levels = cfg["levels"]                      # name -> {score, action}, highest first
        self.label_level = cfg.get("labels", {})
        self.default = cfg.get("default", "medium")
        self.combos = cfg.get("combos", [])
        # phrase must end right before the value: only separators (spaces, ":", "=", "#", "-") between
        self.negative = {
            label: re.compile(r"\b(?:" + "|".join(re.escape(p) for p in phrases) + r")\b[\s:=#.\-]*$", re.I)
            for label, phrases in (cfg.get("negative_context") or {}).items() if phrases}
        for lvl in [*self.label_level.values(), self.default, *(c["level"] for c in self.combos)]:
            if lvl not in self.levels:
                raise ValueError(f"policy level {lvl!r} is not defined under levels")

    def level(self, label):
        return self.label_level.get(label, self.default)

    def to_dict(self):
        return {"levels": self.levels, "labels": self.label_level, "default": self.default,
                "combos": self.combos, "negative_context": {k: v.pattern for k, v in self.negative.items()}}

    def filter_spans(self, text, spans, window=40):
        """Drop findings preceded by a harmless phrase for their label. Returns (kept, dropped)."""
        kept, dropped = [], []
        for sp in spans:
            pat = self.negative.get(sp["label"])
            before = text[max(0, sp["start"] - window):sp["start"]].rsplit("\n", 1)[-1]
            (dropped if pat and pat.search(before) else kept).append(sp)
        return kept, dropped

    def assess(self, spans):
        """spans: dicts with at least "label". Returns the findings with severity and action,
        the combos that fired, and the document risk (level, score, action)."""
        findings = [{**sp, "severity": self.level(sp["label"]),
                     "action": self.levels[self.level(sp["label"])]["action"]} for sp in spans]
        present = {sp["label"] for sp in spans}
        fired = [{"name": c["name"], "level": c["level"], "reason": c.get("reason", "")}
                 for c in self.combos
                 if set(c.get("all", [])) <= present and all(present & set(g) for g in c.get("any", []))]
        counts = {lvl: sum(f["severity"] == lvl for f in findings) for lvl in self.levels}
        levels = [f["severity"] for f in findings] + [c["level"] for c in fired]
        if not levels:
            return {"risk": "none", "score": 0, "action": "allow", "counts": counts, "combos": [],
                    "findings": findings}
        top = max(levels, key=lambda lvl: self.levels[lvl]["score"])
        return {"risk": top, "score": self.levels[top]["score"], "action": self.levels[top]["action"],
                "counts": counts, "combos": fired, "findings": findings}
