// Responses in the engine's shape (datashield_local/engine/core.py scan / scan_document), built
// with the user's policy. Used for the built-in model and to re-assess engine results, so the
// settings page decides what is blocked in both detection modes.

import { maskText } from "./decode.js";

/** "4111 1111 1111 1111" -> "41••••••••11": enough to recognise, not enough to leak. */
export function preview(value) {
  const v = [...(value || "").trim()];
  if (v.length <= 4) return "•".repeat(v.length);
  return v.slice(0, 2).join("") + "•".repeat(Math.min(v.length - 4, 8)) + v.slice(-2).join("");
}

/** text scan: {spans, assessment, masked, ms, chars, suppressed} */
export function textResult(text, spans, suppressed, policy, ms) {
  return { spans, assessment: policy.assess(spans), masked: maskText(text, spans), ms: Math.round(ms * 10) / 10,
           chars: text.length, suppressed };
}

/** file scan: one assessment for the whole file; findings carry location and preview, not values. */
export function fileResult(name, size, det, policy, ms) {
  const a = policy.assess(det.found);
  return {
    name, bytes: size, ms: Math.round(ms * 10) / 10, risk: a.risk, action: a.action, counts: a.counts, combos: a.combos,
    findings: a.findings.map((f) => ({ label: f.label, severity: f.severity, action: f.action, score: f.score ?? null,
                                       source: f.source ?? null, location: f.loc, preview: preview(f.value) })),
    suppressed: det.suppressed, segments: det.segments, skipped: det.skipped, truncated: det.truncated,
  };
}

/** Engine-mode text result re-assessed with the user's policy (the engine already filtered). */
export function reassessText(result, policy) {
  return { ...result, assessment: policy.assess(result.spans) };
}

/** Engine-mode file result re-assessed: findings keep location and preview. */
export function reassessFile(result, policy) {
  const a = policy.assess(result.findings);
  return { ...result, risk: a.risk, action: a.action, counts: a.counts, combos: a.combos,
           findings: a.findings.map(({ label, severity, action, score, source, location, preview: p }) =>
             ({ label, severity, action, score, source, location, preview: p })) };
}
