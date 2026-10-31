// The built-in detector: tokenizer + ONNX model (onnxruntime-web) + decoding, rules and thresholds.
// Same results as datashield_local.common.Predictor (runtime.test.js checks the full pipeline).

import { decode, softmax } from "./decode.js";
import { Tokenizer } from "./tokenizer.js";

export class Detector {
  /**
   * ort: the onnxruntime-web module (passed in: the extension and Node load it differently)
   * files: {model: ArrayBuffer|Uint8Array, tokenizer: object, config: object, thresholds: object}
   */
  static async create(ort, files, { threads = 1 } = {}) {
    ort.env.wasm.numThreads = threads;
    const session = await ort.InferenceSession.create(files.model instanceof Uint8Array ? files.model : new Uint8Array(files.model), {
      executionProviders: ["wasm"],
      graphOptimizationLevel: "all",
      enableCpuMemArena: false,
    });
    return new Detector(ort, session, files);
  }

  constructor(ort, session, files) {
    this.ort = ort;
    this.session = session;
    this.tokenizer = new Tokenizer(files.tokenizer);
    this.id2label = files.config.id2label;
    this.tags = Object.keys(this.id2label).length;
    this.thresholds = files.thresholds || {};
  }

  /** One window -> softmax probabilities per token. */
  async _probs(ids) {
    const n = ids.length;
    const input = new this.ort.Tensor("int64", BigInt64Array.from(ids, BigInt), [1, n]);
    const mask = new this.ort.Tensor("int64", new BigInt64Array(n).fill(1n), [1, n]);
    const out = await this.session.run({ input_ids: input, attention_mask: mask });
    const probs = softmax(out.logits.data, n, this.tags);
    input.dispose?.();
    mask.dispose?.();
    out.logits.dispose?.();
    return probs;
  }

  /** text -> spans [{start, end, label, value, score, source?}] (UTF-16 offsets). */
  async detect(text, { useRules = true } = {}) {
    if (!text || !text.trim()) return [];
    const { windows, offsets } = this.tokenizer.windows(text);
    const probs = [];
    for (const w of windows) probs.push(await this._probs(w.ids));
    return decode(text, windows, offsets, probs, { id2label: this.id2label, thresholds: this.thresholds, useRules });
  }

  async release() {
    await this.session.release();
  }
}
