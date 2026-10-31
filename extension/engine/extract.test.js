// File extraction parity with datashield_local/engine/extract.py (fixtures/extract.json).
import assert from "node:assert";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

import { extractBytes } from "./extract.js";

const FIX = new URL("fixtures/extract.json", import.meta.url);
const squash = (s) => s.replace(/\s+/g, " ").trim();

async function nodePdfText(bytes) {
  const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
  const doc = await pdfjs.getDocument({ data: new Uint8Array(bytes), isEvalSupported: false }).promise;
  const pages = [];
  for (let i = 1; i <= doc.numPages; i++) {
    const content = await (await doc.getPage(i)).getTextContent();
    pages.push(content.items.map((it) => it.str + (it.hasEOL ? "\n" : "")).join(""));
  }
  return pages;
}

test("every format gives the same text, members, pages, locations and skips", { skip: !existsSync(FIX) }, async () => {
  for (const f of JSON.parse(readFileSync(FIX, "utf-8"))) {
    const got = await extractBytes(f.name, new Uint8Array(Buffer.from(f.b64, "base64")), { pdfText: nodePdfText });
    assert.deepStrictEqual(got.skipped, f.skipped, f.name);
    assert.strictEqual(got.segments.length, f.segments.length, f.name);
    got.segments.forEach((s, i) => {
      const want = f.segments[i];
      assert.deepStrictEqual([s.kind, s.member, s.page], [want.kind, want.member, want.page], f.name);
      if (s.kind === "pdf") {
        // pypdf and pdf.js lay out PDF text differently; the words must be the same
        assert.strictEqual(squash(s.text), squash(want.text), f.name);
        return;
      }
      assert.strictEqual(s.text, want.text, f.name);
      for (const [pos, loc] of want.locs) assert.deepStrictEqual(s.locate(pos), loc, `${f.name} @${pos}`);
    });
  }
});
