// Severity, action and document risk for detected spans (port of datashield_local/policy.py).
// The policy object is datashield_local/policy.yaml as JSON (built into policy.json by
// tests/make_extension_assets.py); the settings page overrides `labels` and `actions` per user.

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
export const ACTION_RANK = { allow: 0, warn: 1, mask: 2, block: 3 };

export class Policy {
  /**
   * cfg: {levels: {name: {score, action}}, labels: {LABEL: level}, default, combos, negative_context}
   * overrides (from settings): {labels: {LABEL: level}, actions: {level: action},
   *                             labelActions: {LABEL: action}}
   */
  constructor(cfg, overrides = {}) {
    this.levels = {};
    for (const [name, lv] of Object.entries(cfg.levels)) {
      this.levels[name] = { ...lv, action: overrides.actions?.[name] ?? lv.action };
    }
    this.labelLevel = { ...(cfg.labels || {}), ...(overrides.labels || {}) };
    this.default = cfg.default || "medium";
    this.combos = cfg.combos || [];
    this.labelAction = overrides.labelActions || {};       // per-label action, wins over the level's
    this.negative = {};
    for (const [label, phrases] of Object.entries(cfg.negative_context || {})) {
      if (phrases && phrases.length) {
        this.negative[label] = new RegExp(`\\b(?:${phrases.map(escapeRe).join("|")})\\b[\\s:=#.\\-]*$`, "i");
      }
    }
  }

  level(label) {
    return this.labelLevel[label] ?? this.default;
  }

  action(label) {
    return this.labelAction[label] ?? this.levels[this.level(label)].action;
  }

  /** Drop findings preceded by a harmless phrase for their label (same line, 40 chars). */
  filterSpans(text, spans, window = 40) {
    const kept = [];
    const dropped = [];
    for (const sp of spans) {
      const pat = this.negative[sp.label];
      const before = text.slice(Math.max(0, sp.start - window), sp.start).split("\n").pop();
      (pat && pat.test(before) ? dropped : kept).push(sp);
    }
    return { kept, dropped };
  }

  /** Findings with severity and action, combos that fired, and the document risk. */
  assess(spans) {
    const findings = spans.map((sp) => ({ ...sp, severity: this.level(sp.label), action: this.action(sp.label) }));
    const present = new Set(spans.map((sp) => sp.label));
    const combos = this.combos
      .filter((c) => (c.all || []).every((l) => present.has(l)) && (c.any || []).every((g) => g.some((l) => present.has(l))))
      .map((c) => ({ name: c.name, level: c.level, reason: c.reason || "" }));
    const counts = Object.fromEntries(Object.keys(this.levels).map((lv) => [lv, findings.filter((f) => f.severity === lv).length]));
    const levels = [...findings.map((f) => f.severity), ...combos.map((c) => c.level)];
    if (!levels.length) return { risk: "none", score: 0, action: "allow", counts, combos: [], findings };
    let top = levels[0];
    for (const lv of levels) if (this.levels[lv].score > this.levels[top].score) top = lv;
    // the strictest action of any finding or combo: with per-label actions from the settings a
    // low-severity label can still be set to block (with the default policy this equals the
    // action of the top level, as in policy.py)
    const actions = [...findings.map((f) => f.action), ...combos.map((c) => this.levels[c.level].action)];
    const action = actions.reduce((a, b) => (ACTION_RANK[b] > ACTION_RANK[a] ? b : a), "allow");
    return { risk: top, score: this.levels[top].score, action, counts, combos, findings };
  }
}
