// What the built-in mode computes in the offscreen page: detection + harmless-context filtering.
// The background worker turns this into the engine's response shape with the user's policy
// (results.js), so both detection modes look the same to the content script.

import { extractBytes } from "./extract.js";

export const MAX_TEXT = 200_000;                 // same as the engine's MAX_CHARS
const CHUNK = 100_000;                           // engine CHUNK_CHARS

/** (offset, piece) pieces of at most `size` chars, cut after a newline when possible (engine chunks()). */
export function* chunks(text, size = CHUNK) {
  for (let start = 0; start < text.length;) {
    let end = Math.min(start + size, text.length);
    if (end < text.length) {
      const nl = text.lastIndexOf("\n", end - 1);
      if (nl >= start + size / 2) end = nl + 1;
    }
    yield [start, text.slice(start, end)];
    start = end;
  }
}

/** Text -> {spans (kept), suppressed: [{label, start, end}]}. */
export async function detectText(detector, policy, text) {
  if (text.length > MAX_TEXT) throw Object.assign(new Error(`text longer than ${MAX_TEXT} characters`), { code: "too_long" });
  const { kept, dropped } = policy.filterSpans(text, await detector.detect(text));
  return { spans: kept, suppressed: dropped.map(({ label, start, end }) => ({ label, start, end })) };
}

/**
 * File bytes -> {found: [span + loc], suppressed, segments, skipped, truncated}.
 * onProgress(done, total) in characters, for the "Checking file..." indicator.
 */
export async function detectFile(detector, policy, name, bytes, { pdfText, onProgress = () => {} } = {}) {
  const ex = await extractBytes(name, bytes, { pdfText });
  const total = ex.segments.reduce((a, s) => a + s.text.length, 0);
  const found = [];
  let suppressed = 0;
  let done = 0;
  for (const seg of ex.segments) {
    for (const [off, piece] of chunks(seg.text)) {
      const { kept, dropped } = policy.filterSpans(piece, await detector.detect(piece));
      suppressed += dropped.length;
      for (const s of kept) found.push({ ...s, start: off + s.start, end: off + s.end, loc: seg.locate(off + s.start) });
      done += piece.length;
      onProgress(done, total);
    }
  }
  return { found, suppressed, segments: ex.segments.length, skipped: ex.skipped, truncated: ex.truncated };
}
