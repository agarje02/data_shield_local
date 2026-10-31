// Files -> text segments with locations, in the browser (port of datashield_local/engine/extract.py
// for the built-in mode). Text and code, HTML, DOCX, XLSX, PPTX, PDF (pdf.js, passed in) and
// ZIP archives; e-mail (.eml) is read as text. Limits stop zip bombs and huge files; anything not
// read is listed in `skipped`, so the user is never told a file is clean when it was not checked.

const TEXT_EXT = new Set(["txt", "log", "md", "csv", "tsv", "json", "jsonl", "yaml", "yml", "toml", "ini", "cfg",
  "conf", "env", "properties", "xml", "html", "htm", "sql", "sh", "bash", "zsh", "ps1", "bat", "py", "js", "ts",
  "tsx", "jsx", "java", "kt", "go", "rs", "rb", "php", "cs", "c", "h", "cpp", "hpp", "swift", "scala", "tf",
  "tfvars", "gradle", "pem", "key", "pub", "crt", "npmrc", "pypirc", "netrc", "dockerfile", "gitconfig", "rtf",
  "srt", "vtt", "eml"]);
const TEXT_NAMES = new Set(["dockerfile", "makefile", ".env", ".npmrc", ".netrc", ".pypirc", ".gitconfig", "id_rsa",
  "id_ed25519", "credentials", "config"]);
const IMAGE_EXT = new Set(["png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp", "heic"]);

export const LIMITS = {
  maxFileBytes: 25_000_000,
  maxTextChars: 300_000,      // the built-in model reads ~1,000 characters in well under a second
  maxDepth: 3,
  maxMembers: 500,
  maxTotalBytes: 150_000_000,
  maxRatio: 100,
};

// ---------------------------------------------------------------- segments

export class Segment {
  constructor(text, kind, member = "", extra = {}) {
    Object.assign(this, { text, kind, member, page: null, cells: [], ...extra });
    this._lines = null;
  }

  /** Where character `start` is: {member?, line|paragraph, col, page?} or {member?, sheet, cell}. */
  locate(start) {
    const loc = this.member ? { member: this.member } : {};
    if (this.kind === "xlsx") {
      let i = -1;
      for (let k = 0; k < this.cells.length && this.cells[k][0] <= start; k++) i = k;
      if (i >= 0) Object.assign(loc, { sheet: this.cells[i][2], cell: this.cells[i][3] });
      return loc;
    }
    if (!this._lines) {
      this._lines = [0];
      for (let i = this.text.indexOf("\n"); i !== -1; i = this.text.indexOf("\n", i + 1)) this._lines.push(i + 1);
    }
    let line = 0;
    while (line < this._lines.length && this._lines[line] <= start) line++;
    loc[this.kind === "docx" || this.kind === "pptx" ? "paragraph" : "line"] = line;
    loc.col = start - this._lines[line - 1] + 1;
    if (this.page !== null) loc.page = this.page;
    return loc;
  }
}

// ---------------------------------------------------------------- helpers

const extOf = (name) => {
  const base = name.replace(/\\/g, "/").split("/").pop().toLowerCase();
  const dot = base.lastIndexOf(".");
  return { base, ext: dot > 0 ? base.slice(dot + 1) : "" };
};
const join = (member, name) => (member ? `${member} > ${name}` : name);

function decodeText(bytes) {
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) return new TextDecoder("utf-8").decode(bytes.subarray(3));
  if (bytes[0] === 0xff && bytes[1] === 0xfe) return new TextDecoder("utf-16le").decode(bytes.subarray(2));
  if (bytes[0] === 0xfe && bytes[1] === 0xff) return new TextDecoder("utf-16be").decode(bytes.subarray(2));
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return new TextDecoder("latin1").decode(bytes);
  }
}

export function looksBinary(bytes) {
  const head = bytes.subarray(0, 8192);
  const bom16 = (head[0] === 0xff && head[1] === 0xfe) || (head[0] === 0xfe && head[1] === 0xff);
  if (head.includes(0) && !bom16) return true;
  let ctrl = 0;
  for (const b of head) if (b < 9 || (b > 13 && b < 32)) ctrl++;
  return head.length > 0 && ctrl / head.length > 0.1;
}

const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };
export function unescapeXml(s) {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
    if (e[0] === "#") {
      const cp = e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      return cp > 0 && cp <= 0x10ffff ? String.fromCodePoint(cp) : m;
    }
    return ENTITIES[e.toLowerCase()] ?? m;
  });
}

const htmlText = (html) => unescapeXml(html.replace(/<(script|style)\b[\s\S]*?<\/\1>|<[^>]+>/gi, " "));
// Inner XML of each <tag> element, empty <tag/> included ("w:p" does not match <w:pPr>).
const elements = (xml, tag) => [...xml.matchAll(new RegExp(`<${tag}(?:\\s[^>]*?)?(?:/>|>([\\s\\S]*?)</${tag}>)`, "g"))]
  .map((m) => m[1] || "");
const textsOf = (xml, tag) => elements(xml, tag).map(unescapeXml);
const paragraphs = (xml, p, t) => elements(xml, p).map((para) => textsOf(para, t).join(""));

// ---------------------------------------------------------------- zip

/** Central-directory listing of a zip: [{name, method, csize, size, offset, encrypted}]. */
export function zipEntries(bytes) {
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let eocd = -1;
  for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 65557); i--) {
    if (v.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw Object.assign(new Error("not a zip"), { code: "bad_zip" });
  const count = v.getUint16(eocd + 10, true);
  let p = v.getUint32(eocd + 16, true);
  const out = [];
  const dec = new TextDecoder();
  for (let i = 0; i < count; i++) {
    if (p + 46 > bytes.length || v.getUint32(p, true) !== 0x02014b50) throw Object.assign(new Error("bad zip"), { code: "bad_zip" });
    const flags = v.getUint16(p + 8, true);
    const nameLen = v.getUint16(p + 28, true);
    const extraLen = v.getUint16(p + 30, true);
    const commentLen = v.getUint16(p + 32, true);
    out.push({ name: dec.decode(bytes.subarray(p + 46, p + 46 + nameLen)), method: v.getUint16(p + 10, true),
               csize: v.getUint32(p + 20, true), size: v.getUint32(p + 24, true), offset: v.getUint32(p + 42, true),
               encrypted: Boolean(flags & 1) });
    p += 46 + nameLen + extraLen + commentLen;
  }
  return out;
}

export async function zipRead(bytes, e) {
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (v.getUint32(e.offset, true) !== 0x04034b50) throw Object.assign(new Error("bad zip"), { code: "bad_zip" });
  const start = e.offset + 30 + v.getUint16(e.offset + 26, true) + v.getUint16(e.offset + 28, true);
  const data = bytes.subarray(start, start + e.csize);
  if (e.method === 0) return data;
  if (e.method !== 8) throw new Error(`zip method ${e.method}`);
  const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  const out = new Uint8Array(await new Response(stream).arrayBuffer());
  if (out.length !== e.size) throw Object.assign(new Error("bad zip"), { code: "bad_zip" });
  return out;
}

async function zipFiles(bytes) {
  const entries = zipEntries(bytes);
  const map = new Map(entries.map((e) => [e.name, e]));
  const read = async (name) => (map.has(name) ? decodeText(await zipRead(bytes, map.get(name))) : null);
  return { entries, read };
}

// ---------------------------------------------------------------- extractor

export async function extractBytes(name, bytes, { limits = LIMITS, pdfText = null } = {}) {
  const out = { segments: [], skipped: [], truncated: false };
  let chars = 0;
  let total = 0;
  const add = (seg) => {
    const room = limits.maxTextChars - chars;
    if (room <= 0) { out.truncated = true; return; }
    if (seg.text.length > room) { seg.text = seg.text.slice(0, room); out.truncated = true; }
    if (seg.text.trim()) { chars += seg.text.length; out.segments.push(seg); }
  };
  const skip = (member, reason) => out.skipped.push({ member, reason });

  async function run(fname, data, member = "", depth = 0) {
    total += data.length;
    if (data.length > limits.maxFileBytes) return skip(member || fname, `larger than ${limits.maxFileBytes / 1e6} MB`);
    if (total > limits.maxTotalBytes) return skip(member || fname, "archive total size limit reached");
    const { ext, base } = extOf(fname);
    try {
      const isPdf = ext === "pdf" || decodeText(data.subarray(0, 5)) === "%PDF-";
      if (isPdf) {
        if (!pdfText) return skip(member || fname, "PDF reader not available");
        const pages = await pdfText(data);
        let got = false;
        pages.forEach((t, i) => { if (t.trim()) { got = true; add(new Segment(t, "pdf", member, { page: i + 1 })); } });
        if (!got) skip(member || fname, "PDF has no text layer (scanned image; OCR not enabled)");
        return;
      }
      if (ext === "docx") {
        const z = await zipFiles(data);
        const parts = ["word/document.xml", ...z.entries.map((e) => e.name).filter((n) => /^word\/(header|footer)\d*\.xml$/.test(n)).sort()];
        const paras = [];
        for (const p of parts) {
          const xml = await z.read(p);
          if (xml) paras.push(...paragraphs(xml, "w:p", "w:t"));
        }
        return add(new Segment(paras.join("\n"), "docx", member));
      }
      if (ext === "pptx") {
        const z = await zipFiles(data);
        const slides = z.entries.map((e) => e.name).filter((n) => /^ppt\/slides\/slide\d+\.xml$/.test(n))
          .sort((a, b) => +a.match(/\d+/)[0] - +b.match(/\d+/)[0]);
        const paras = [];
        for (const s of slides) paras.push(...paragraphs(await z.read(s), "a:p", "a:t"));
        return add(new Segment(paras.join("\n"), "pptx", member));
      }
      if (ext === "xlsx" || ext === "xlsm") return await xlsx(await zipFiles(data), member);
      if (ext === "zip" || ext === "jar") return await zip(data, member, depth);
      if (IMAGE_EXT.has(ext)) return skip(member || fname, "image (OCR not enabled)");
      if (TEXT_EXT.has(ext) || TEXT_NAMES.has(base) || base.startsWith(".env") || !looksBinary(data)) {
        let text = decodeText(data);
        if (ext === "html" || ext === "htm") text = htmlText(text);
        return add(new Segment(text, "text", member));
      }
      return skip(member || fname, "binary file");
    } catch (e) {
      return skip(member || fname, e.code === "bad_zip" ? "damaged or not a real archive/Office file" : `could not read: ${e.name}`);
    }
  }

  async function xlsx(z, member) {
    const shared = [];
    const ss = await z.read("xl/sharedStrings.xml");
    if (ss) shared.push(...paragraphs(ss, "si", "t"));
    const rels = {};
    const relXml = await z.read("xl/_rels/workbook.xml.rels");
    if (relXml) {
      for (const m of relXml.matchAll(/<Relationship\b[^>]*>/g)) {
        const id = m[0].match(/\bId="([^"]*)"/);
        const target = m[0].match(/\bTarget="([^"]*)"/);
        if (id && target) rels[id[1]] = target[1].replace(/^\/+/, "").replace(/^xl\//, "");
      }
    }
    const wb = (await z.read("xl/workbook.xml")) || "";
    for (const m of wb.matchAll(/<sheet\b[^>]*>/g)) {
      const sheet = unescapeXml((m[0].match(/\bname="([^"]*)"/) || [])[1] || "");
      const rid = (m[0].match(/\br:id="([^"]*)"/) || [])[1];
      const xml = rels[rid] ? await z.read(`xl/${rels[rid]}`) : null;
      if (!xml) continue;
      // one line per row, cells joined by " | ": the table layout the model was trained on
      const parts = [];
      const cells = [];
      let pos = 0;
      for (const row of elements(xml, "row")) {
        const line = [];
        for (const c of row.matchAll(/<c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g)) {
          const attrs = c[1];
          const body = c[2] || "";
          const t = (attrs.match(/\bt="([^"]*)"/) || [])[1];
          const ref = (attrs.match(/\br="([^"]*)"/) || [])[1];
          const v = textsOf(body, "v")[0];
          let val;
          if (t === "s" && v !== undefined) val = shared[+v] ?? "";
          else if (t === "inlineStr") val = textsOf(body, "t").join("");
          else val = v ?? "";
          if (val === "") continue;
          if (line.length) { line.push(" | "); pos += 3; }
          cells.push([pos, pos + val.length, sheet, ref]);
          line.push(val);
          pos += val.length;
        }
        if (line.length) { parts.push(line.join("") + "\n"); pos += 1; }
      }
      add(new Segment(parts.join(""), "xlsx", member, { cells, sheet }));
    }
  }

  async function zip(data, member, depth) {
    if (depth + 1 > limits.maxDepth) return skip(member || "zip", "archive nested too deep");
    let entries = zipEntries(data).filter((e) => !e.name.endsWith("/"));
    if (entries.length > limits.maxMembers) {
      skip(member || "zip", `more than ${limits.maxMembers} files; only the first are scanned`);
      entries = entries.slice(0, limits.maxMembers);
    }
    for (const e of entries) {
      const inner = join(member, e.name);
      if (e.encrypted) { skip(inner, "encrypted archive member"); continue; }
      if (e.csize && e.size / e.csize > limits.maxRatio) { skip(inner, "compression ratio too high (possible zip bomb)"); continue; }
      if (e.size > limits.maxFileBytes) { skip(inner, `larger than ${limits.maxFileBytes / 1e6} MB`); continue; }
      if (total + e.size > limits.maxTotalBytes) { skip(inner, "archive total size limit reached"); break; }
      await run(e.name, await zipRead(data, e), inner, depth + 1);
    }
  }

  await run(name, bytes);
  return out;
}
