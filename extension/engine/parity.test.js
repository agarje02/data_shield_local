// Parity with the Python detector: node --test extension/engine/
// Needs the reference outputs from `python tests/make_js_fixtures.py` (skipped when missing).
import assert from "node:assert";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

import { aggregateWords, applyThresholds, decode, repairBoundaries, tokensToSpans } from "./decode.js";
import { Policy } from "./policy.js";
import { isValid, rescue } from "./rules.js";
import { Tokenizer } from "./tokenizer.js";

const HERE = new URL(".", import.meta.url);
const ROOT = new URL("../../", HERE);
const fixture = (name) => new URL(`fixtures/${name}`, HERE);
const load = (url) => JSON.parse(readFileSync(url, "utf-8"));
const have = (name) => existsSync(fixture(name));
const MODEL = new URL("models/deberta_v2_3_onnx_q4/", ROOT);
const key = (sp) => `${sp.label}|${sp.start}|${sp.end}`;

test("tokenizer: same ids, offsets and windows as Hugging Face", { skip: !have("tokenizer.json") }, () => {
  const tok = new Tokenizer(load(new URL("tokenizer.json", MODEL)));
  const rows = load(fixture("tokenizer.json"));
  let bad = 0;
  for (const r of rows) {
    const { ids, offsets } = tok.encode(r.text);
    const got = { ids: [tok.clsId, ...ids, tok.sepId], offsets: [[0, 0], ...offsets, [0, 0]] };
    const wins = tok.windows(r.text).windows.map((w) => w.ids);
    const same = JSON.stringify(got.ids) === JSON.stringify(r.ids)
      && JSON.stringify(got.offsets) === JSON.stringify(r.offsets)
      && JSON.stringify(wins) === JSON.stringify(r.windows);
    if (!same && bad++ < 3) {
      const i = got.ids.findIndex((x, k) => x !== r.ids[k] || JSON.stringify(got.offsets[k]) !== JSON.stringify(r.offsets[k]));
      console.log("MISMATCH", JSON.stringify(r.text.slice(0, 80)), "at token", i,
        { js: [got.ids.slice(i, i + 3), got.offsets.slice(i, i + 3)], py: [r.ids.slice(i, i + 3), r.offsets.slice(i, i + 3)] },
        "windows", wins.length, r.windows.length);
    }
  }
  assert.strictEqual(bad, 0, `${bad} of ${rows.length} texts tokenized differently`);
});

test("decoding: same words and spans from the same probabilities", { skip: !have("decode.json") }, () => {
  const { id2label, docs } = load(fixture("decode.json"));
  const types = new Set(Object.values(id2label).filter((t) => t !== "O").map((t) => t.slice(2)));
  for (const d of docs) {
    const words = aggregateWords(d.text, d.tokens, id2label);
    assert.deepStrictEqual(words.map(([s, e, l]) => [s, e, l]), d.words.map(([s, e, l]) => [s, e, l]));
    const spans = repairBoundaries(d.text, tokensToSpans(d.text, words, id2label), types);
    assert.deepStrictEqual(spans.map(key), d.spans.map(key));
    spans.forEach((sp, i) => assert.ok(Math.abs(sp.score - d.spans[i].score) < 2e-4));
  }
});

test("rules: same verdict for every value", { skip: !have("rules.json") }, () => {
  const { verdicts, rescue: rescued } = load(fixture("rules.json"));
  const wrong = verdicts.filter(([label, value, ok]) => isValid(label, value) !== ok);
  assert.deepStrictEqual(wrong, []);
  for (const r of rescued) assert.deepStrictEqual(rescue(r.text, []).map(key), r.spans.map(key));
});

test("policy: same kept spans, risk, action and combos", { skip: !have("policy.json") }, () => {
  const policy = new Policy(load(new URL("policy.json", HERE)));
  for (const d of load(fixture("policy.json"))) {
    const { kept, dropped } = policy.filterSpans(d.text, d.spans);
    assert.deepStrictEqual(kept.map(key), d.kept.map(key));
    assert.strictEqual(dropped.length, d.dropped);
    const a = policy.assess(kept);
    assert.deepStrictEqual([a.risk, a.score, a.action, a.counts, a.combos.map((c) => c.name)],
                           [d.risk, d.score, d.action, d.counts, d.combos]);
  }
});

test("rules + thresholds after decoding match Predictor.predict", { skip: !have("decode.json") || !have("e2e.json") }, () => {
  const { id2label, docs } = load(fixture("decode.json"));
  const { thresholds, docs: e2e } = load(fixture("e2e.json"));
  const final = new Map(e2e.map((d) => [d.text, d.spans]));
  const types = new Set(Object.values(id2label).filter((t) => t !== "O").map((t) => t.slice(2)));
  for (const d of docs) {
    const spans = repairBoundaries(d.text, tokensToSpans(d.text, aggregateWords(d.text, d.tokens, id2label), id2label), types);
    const got = applyThresholds(rescue(d.text, spans.filter((sp) => isValid(sp.label, sp.value))), thresholds);
    assert.deepStrictEqual(got.map(key), final.get(d.text).map(key));
  }
  assert.ok(typeof decode === "function");
});
