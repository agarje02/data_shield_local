// The full built-in pipeline (onnxruntime-web WASM + JS decoding) against Predictor.predict.
// Needs `npm install` in extension/, the q4 model in models/deberta_v2_3_onnx_q4/ and
// the fixtures from `python tests/make_js_fixtures.py`; skipped when any is missing.
import assert from "node:assert";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";

import { Detector } from "./detector.js";

const HERE = new URL(".", import.meta.url);
const MODEL = new URL("../../models/deberta_v2_3_onnx_q4/", HERE);
const E2E = new URL("fixtures/e2e.json", HERE);
const require = createRequire(import.meta.url);
let ort = null;
try { ort = require("onnxruntime-web"); } catch { /* not installed */ }
const ready = ort && existsSync(new URL("model.onnx", MODEL)) && existsSync(E2E);
const load = (url) => JSON.parse(readFileSync(url, "utf-8"));
const key = (sp) => `${sp.label}|${sp.start}|${sp.end}`;

test("built-in detector finds the same spans as the Python predictor", { skip: !ready, timeout: 1_800_000 }, async () => {
  const detector = await Detector.create(ort, {
    model: readFileSync(new URL("model.onnx", MODEL)),
    tokenizer: load(new URL("tokenizer.json", MODEL)),
    config: load(new URL("config.json", MODEL)),
    thresholds: load(new URL("thresholds.json", MODEL)),
  }, { threads: 4 });
  const docs = load(E2E).docs;
  let same = 0;
  let pyTotal = 0;
  let jsTotal = 0;
  let both = 0;
  const diffs = [];
  const t0 = performance.now();
  for (const d of docs) {
    const got = await detector.detect(d.text);
    const a = new Set(got.map(key));
    const b = new Set(d.spans.map(key));
    pyTotal += b.size;
    jsTotal += a.size;
    both += [...a].filter((k) => b.has(k)).length;
    if (a.size === b.size && [...a].every((k) => b.has(k))) same++;
    else if (diffs.length < 8) diffs.push({ text: d.text.slice(0, 60), js: [...a].filter((k) => !b.has(k)), py: [...b].filter((k) => !a.has(k)) });
  }
  const ms = (performance.now() - t0) / docs.length;
  const agree = (2 * both) / (pyTotal + jsTotal);
  console.log(`docs identical ${same}/${docs.length}, span agreement ${(agree * 100).toFixed(2)}%, ${ms.toFixed(0)} ms/doc`);
  for (const x of diffs) console.log(JSON.stringify(x));
  await detector.release();
  // numeric differences between onnxruntime builds (and padding) may flip a rare borderline token
  assert.ok(agree >= 0.995, `span agreement ${agree}`);
});
