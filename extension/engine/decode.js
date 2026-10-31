// Token probabilities -> character spans (port of the decoding in datashield_local/common.py;
// decode.test.js feeds both the same probabilities and compares the spans).

import { applyRules, isSpace } from "./rules.js";

const TRIM_TRAILING = ".,;:!?)]}\"'|=>";
const TRIM_LEADING = "([{\"';,|=:<";
const NO_PUNCT_TRIM = new Set(["PASSWORD"]);
const KEEP_LEADING_BRACKET = new Set(["PHONE"]);
const WORD_DELIMS = new Set(";,|\"'=:<>[]{}");
const YEAR_AFTER = /,?\s?(?:19|20)\d{2}\b/y;
const HAS_YEAR = /(?:19|20)\d{2}/;
const USER_PASS = /^([A-Za-z][\w.-]{2,63}):(\S+)$/;
const isAlnum = (s) => /[\p{L}\p{N}]/u.test(s);

/** Keep, per token offset, the window where the token sits furthest from a window edge. */
export function bestTokens(windows, offsets, probs) {
  const best = new Map();
  windows.forEach((w, wi) => {
    const n = w.ids.length - 2;                  // content tokens, without [CLS] / [SEP]
    for (let k = 0; k < n; k++) {
      const [s, e] = offsets[w.start + k];
      if (s === e) continue;
      const centrality = Math.min(k, n - 1 - k);
      const key = `${s},${e}`;
      const prev = best.get(key);
      if (!prev || centrality > prev.c) best.set(key, { s, e, c: centrality, p: probs[wi][k + 1] });
    }
  });
  return [...best.values()].sort((a, b) => a.s - b.s || a.e - b.e).map(({ s, e, p }) => [s, e, p]);
}

/** Word index per token: a word is a run without whitespace or WORD_DELIMS; a delimiter is its own word. */
export function wordIds(text, tokens) {
  const ids = [];
  let wid = -1;
  let prevEnd = null;
  for (const [s, e] of tokens) {
    let first = s;
    while (first < e && isSpace(text[first])) first++;
    const piece = text.slice(first, e);
    const gap = prevEnd !== null ? text.slice(prevEnd, first) : " ";
    if (prevEnd === null || gap !== "" || (piece && WORD_DELIMS.has(piece[0]))
        || (prevEnd > 0 && WORD_DELIMS.has(text[prevEnd - 1]))) wid++;
    ids.push(wid);
    prevEnd = e;
  }
  return ids;
}

/** Every sub-word of a word gets one label: the type with the highest summed B+I probability. */
export function aggregateWords(text, tokens, id2label) {
  const label2id = Object.fromEntries(Object.entries(id2label).map(([k, v]) => [v, +k]));
  const types = [...new Set(Object.values(id2label).filter((t) => t !== "O").map((t) => t.split("-")[1]))].sort();
  const oId = label2id.O;
  const ids = wordIds(text, tokens);
  const out = [];
  for (let i = 0; i < tokens.length;) {
    let j = i;
    while (j + 1 < tokens.length && ids[j + 1] === ids[i]) j++;
    const group = tokens.slice(i, j + 1);
    let voters = group.filter(([s, e]) => isAlnum(text.slice(s, e)));
    if (!voters.length) voters = group;
    const n = voters.length;
    const scores = { O: voters.reduce((a, [, , p]) => a + p[oId], 0) / n };
    for (const t of types) {
      scores[t] = voters.reduce((a, [, , p]) => a + p[label2id[`B-${t}`]] + p[label2id[`I-${t}`]], 0) / n;
    }
    let best = "O";
    for (const k of Object.keys(scores)) if (scores[k] > scores[best]) best = k;   // first max, like Python max()
    let lid = oId;
    if (best !== "O") {
      const first = group[0][2];
      lid = label2id[`${first[label2id[`B-${best}`]] >= first[label2id[`I-${best}`]] ? "B" : "I"}-${best}`];
    }
    out.push([group[0][0], group[group.length - 1][1], lid, scores[best]]);
    i = j + 1;
  }
  return out;
}

function trim(text, start, end, label) {
  while (start < end && isSpace(text[start])) start++;
  while (end > start && isSpace(text[end - 1])) end--;
  if (!NO_PUNCT_TRIM.has(label)) {
    while (end > start && TRIM_TRAILING.includes(text[end - 1])) end--;
    const leading = KEEP_LEADING_BRACKET.has(label) ? TRIM_LEADING.replace("(", "") : TRIM_LEADING;
    while (start < end && leading.includes(text[start])) start++;
  } else {
    if (end > start + 1 && ".,;?".includes(text[end - 1]) && (end === text.length || isSpace(text[end]))) end--;
    while (end > start + 1 && ":=".includes(text[start])) start++;
  }
  return [start, end];
}

const round4 = (x) => Math.round(x * 1e4) / 1e4;

/** Words (start, end, label id, prob) -> spans: B-/I- runs joined, never across a line break. */
export function tokensToSpans(text, words, id2label) {
  const spans = [];
  let cur = null;
  const close = () => {
    if (!cur) return;
    const [s, e] = trim(text, cur.start, cur.end, cur.label);
    if (s < e) {
      spans.push({ start: s, end: e, label: cur.label, value: text.slice(s, e),
                   score: round4(cur.probs.reduce((a, b) => a + b, 0) / cur.probs.length) });
    }
  };
  for (const [s, e, lid, prob] of words) {
    const tag = id2label[lid];
    if (tag === "O") {
      close();
      cur = null;
      continue;
    }
    const [prefix, lab] = [tag.slice(0, 1), tag.slice(2)];
    let lead = s;
    while (lead < e && isSpace(text[lead])) lead++;
    if (cur && prefix === "I" && lab === cur.label && !text.slice(cur.end, lead).includes("\n")) {
      cur.end = Math.max(cur.end, e);
      cur.probs.push(prob);
    } else {
      close();
      cur = { start: s, end: e, label: lab, probs: [prob] };
    }
  }
  close();
  return spans;
}

/** DATE_OF_BIRTH "May 01" + ", 1984" -> over the year; PASSWORD "user:pass" -> USERNAME + PASSWORD. */
export function repairBoundaries(text, spans, labelTypes) {
  const out = [];
  for (const sp of spans) {
    if (sp.label === "DATE_OF_BIRTH" && !HAS_YEAR.test(sp.value)) {
      YEAR_AFTER.lastIndex = sp.end;
      const m = YEAR_AFTER.exec(text);
      if (m) {
        sp.end = m.index + m[0].length;
        sp.value = text.slice(sp.start, sp.end);
      }
    }
    const m = sp.label === "PASSWORD" && labelTypes.has("USERNAME") ? sp.value.match(USER_PASS) : null;
    if (m) {
      const s = sp.start;
      out.push({ ...sp, end: s + m[1].length, label: "USERNAME", value: m[1] });
      out.push({ ...sp, start: s + m[1].length + 1, value: m[2] });
      continue;
    }
    out.push(sp);
  }
  return out;
}

export function applyThresholds(spans, thresholds) {
  if (!thresholds) return spans;
  const dflt = thresholds.default ?? 0;
  return spans.filter((sp) => sp.score >= (thresholds[sp.label] ?? dflt));
}

export function maskText(text, spans) {
  let out = "";
  let last = 0;
  for (const sp of [...spans].sort((a, b) => a.start - b.start)) {
    if (sp.start < last) continue;
    out += text.slice(last, sp.start) + `[${sp.label}]`;
    last = sp.end;
  }
  return out + text.slice(last);
}

/** Softmax over each token's logits: Float32Array [windows x tokens x tags] -> nested arrays. */
export function softmax(logits, tokens, tags) {
  const out = [];
  for (let t = 0; t < tokens; t++) {
    const row = logits.subarray(t * tags, (t + 1) * tags);
    let max = -Infinity;
    for (const x of row) if (x > max) max = x;
    let sum = 0;
    const e = Array.from(row, (x) => { const v = Math.exp(x - max); sum += v; return v; });
    out.push(e.map((v) => v / sum));
  }
  return out;
}

/** The whole pipeline after the model: probabilities per window -> final spans. */
export function decode(text, windows, offsets, probs, { id2label, thresholds, useRules = true }) {
  const labelTypes = new Set(Object.values(id2label).filter((t) => t !== "O").map((t) => t.slice(2)));
  const toks = bestTokens(windows, offsets, probs);
  let spans = repairBoundaries(text, tokensToSpans(text, aggregateWords(text, toks, id2label), id2label), labelTypes);
  if (useRules) spans = applyRules(text, spans);
  return applyThresholds(spans, thresholds);
}
