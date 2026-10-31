// DeBERTa-v3 tokenizer from the model's tokenizer.json, with character offsets: the same tokens
// and offsets as Hugging Face `tokenizers` (checked by tokenizer.test.js on the Python output).
//
//   normalizer     whitespace runs and \n \r \t -> " ", NFC, strip right
//   pre-tokenizer  Metaspace: " " -> "▁", split before each "▁", "▁" prepended to each segment
//   model          SentencePiece Unigram (best-scoring split, unknown chars -> [UNK], runs fused)
//
// Offsets are UTF-16 indexes into the original text, like every JavaScript string index.

const META = "▁";
const WS_RUN = /\p{White_Space}{2,}|[\n\r\t]/gu;
const IS_WS = /^\p{White_Space}$/u;
const MARK = /^\p{M}$/u;
const UNK_PENALTY = 10;

export class Tokenizer {
  constructor(json) {
    const model = json.model;
    if (model.type !== "Unigram") throw new Error(`unsupported tokenizer model ${model.type}`);
    this.vocab = new Map();
    this.scores = new Float64Array(model.vocab.length);
    let min = Infinity;
    let maxLen = 1;
    model.vocab.forEach(([piece, score], id) => {
      this.vocab.set(piece, id);
      this.scores[id] = score;
      if (score < min) min = score;
      maxLen = Math.max(maxLen, [...piece].length);
    });
    this.maxLen = maxLen;
    this.unkId = model.unk_id;
    this.unkScore = min - UNK_PENALTY;
    this.special = new Map(json.added_tokens.filter((t) => t.special).map((t) => [t.content, t.id]));
    this.clsId = this.special.get("[CLS]");
    this.sepId = this.special.get("[SEP]");
    this.padId = this.special.get("[PAD]");
    const esc = [...this.special.keys()].map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    this.specialRe = new RegExp(esc.join("|"), "g");
  }

  /** text -> {ids, offsets} without [CLS]/[SEP]; offsets are [start, end) in UTF-16 units. */
  encode(text) {
    const ids = [];
    const offsets = [];
    let last = 0;
    this.specialRe.lastIndex = 0;
    for (const m of text.matchAll(this.specialRe)) {
      this._segment(text, last, m.index, ids, offsets);
      ids.push(this.special.get(m[0]));
      offsets.push([m.index, m.index + m[0].length]);
      last = m.index + m[0].length;
    }
    this._segment(text, last, text.length, ids, offsets);
    return { ids, offsets };
  }

  /** Normalize text[from:to] keeping, per output char, the original range it came from. */
  _normalize(text, from, to) {
    const chars = [];      // one entry per code point: [char, start, end]
    const seg = text.slice(from, to);
    let pos = 0;
    const pushRaw = (a, b) => {
      for (let i = a; i < b;) {
        const cp = seg.codePointAt(i);
        const w = cp > 0xffff ? 2 : 1;
        chars.push([String.fromCodePoint(cp), from + i, from + i + w]);
        i += w;
      }
    };
    WS_RUN.lastIndex = 0;
    for (const m of seg.matchAll(WS_RUN)) {
      pushRaw(pos, m.index);
      // like `tokenizers`: the new space takes the place of the run's last character
      const end = from + m.index + m[0].length;
      chars.push([" ", end - 1, end]);
      pos = m.index + m[0].length;
    }
    pushRaw(pos, seg.length);

    // NFC per cluster (a character and the combining marks after it)
    const out = [];
    for (let i = 0; i < chars.length;) {
      let j = i + 1;
      while (j < chars.length && MARK.test(chars[j][0])) j++;
      const cluster = chars.slice(i, j);
      const raw = cluster.map((c) => c[0]).join("");
      const nfc = raw.normalize("NFC");
      if (nfc === raw) {
        out.push(...cluster);
      } else {
        // the composed character keeps the base character's place; leftover marks keep the last ones
        const outChars = [...nfc];
        outChars.forEach((ch, k) => {
          const src = k === 0 ? cluster[0] : cluster[cluster.length - (outChars.length - k)];
          out.push([ch, src[1], src[2]]);
        });
      }
      i = j;
    }
    while (out.length && IS_WS.test(out[out.length - 1][0])) out.pop();
    return out;
  }

  _segment(text, from, to, ids, offsets) {
    if (from >= to) return;
    const chars = this._normalize(text, from, to);
    if (!chars.length) return;
    // Metaspace: spaces become "▁" and start a new word; each segment starts with "▁"
    for (const c of chars) if (c[0] === " ") c[0] = META;
    if (chars[0][0] !== META) chars.unshift([META, chars[0][1], chars[0][2]]);   // aligned to the first char
    let start = 0;
    for (let i = 1; i <= chars.length; i++) {
      if (i === chars.length || chars[i][0] === META) {
        this._unigram(chars.slice(start, i), ids, offsets);
        start = i;
      }
    }
  }

  /** Best-scoring split of one word into vocabulary pieces (Viterbi), unknown runs fused. */
  _unigram(word, ids, offsets) {
    const n = word.length;
    const best = new Float64Array(n + 1).fill(-Infinity);
    const from = new Int32Array(n + 1).fill(-1);
    const pick = new Int32Array(n + 1).fill(-1);
    best[0] = 0;
    for (let i = 0; i < n; i++) {
      if (best[i] === -Infinity) continue;
      let piece = "";
      let single = false;
      for (let len = 1; len <= this.maxLen && i + len <= n; len++) {
        piece += word[i + len - 1][0];
        const id = this.vocab.get(piece);
        if (id === undefined || this.special.has(piece)) continue;
        if (len === 1) single = true;
        const score = best[i] + this.scores[id];
        if (score > best[i + len]) {
          best[i + len] = score;
          from[i + len] = i;
          pick[i + len] = id;
        }
      }
      if (!single) {
        const score = best[i] + this.unkScore;
        if (score > best[i + 1]) {
          best[i + 1] = score;
          from[i + 1] = i;
          pick[i + 1] = this.unkId;
        }
      }
    }
    const path = [];
    for (let end = n; end > 0; end = from[end]) path.push([from[end], end, pick[end]]);
    path.reverse();
    let prevUnk = false;
    for (const [a, b, id] of path) {
      const range = [word[a][1], word[b - 1][2]];
      if (id === this.unkId && prevUnk) {
        offsets[offsets.length - 1][1] = range[1];
      } else {
        ids.push(id);
        offsets.push(range);
      }
      prevUnk = id === this.unkId;
    }
  }

  /**
   * Model inputs for a text: windows of at most maxLength tokens ([CLS] + content + [SEP]),
   * consecutive windows sharing `stride` content tokens, as the Python tokenizer makes them.
   * Returns {windows: [{ids, start}], offsets} where window content token k is offsets[start + k].
   */
  windows(text, maxLength = 512, stride = 128) {
    const { ids, offsets } = this.encode(text);
    const room = maxLength - 2;
    const step = room - stride;
    const windows = [];
    for (let start = 0; ; start += step) {
      const part = ids.slice(start, start + room);
      windows.push({ ids: [this.clsId, ...part, this.sepId], start });
      if (start + room >= ids.length) break;
    }
    return { windows, offsets };
  }
}
