// Format and checksum rules that run after the model (port of datashield_local/validators.py and
// checksums.py; rules.test.js compares every verdict with the Python one).
//   verify  drop a span whose value cannot be its label (Aadhaar fails Verhoeff, card fails Luhn, ...)
//   rescue  add spans for secret formats that are unambiguous on their own (AKIA..., JWT, PEM, ...)

// ---------------------------------------------------------------- checksums

const V_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
  [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
  [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
  [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]];
const V_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
  [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 7, 6, 0, 8], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
  [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]];

export function verhoeffValid(digits) {
  if (!/^[0-9]+$/.test(digits)) return false;
  let c = 0;
  [...digits].reverse().forEach((d, i) => { c = V_D[c][V_P[i % 8][+d]]; });
  return c === 0;
}

export function luhnValid(digits) {
  if (!/^[0-9]+$/.test(digits)) return false;
  let total = 0;
  [...digits].reverse().forEach((d, i) => {
    let n = +d;
    if (i % 2 === 1) {
      n *= 2;
      if (n > 9) n -= 9;
    }
    total += n;
  });
  return total % 10 === 0;
}

export function ibanValid(iban) {
  const s = iban.replace(/ /g, "").toUpperCase();
  if (!(s.length >= 15 && s.length <= 34 && /^[A-Z]{2}\d{2}[A-Z0-9]+$/.test(s))) return false;
  let rem = 0;
  for (const ch of s.slice(4) + s.slice(0, 4)) {
    for (const d of String(parseInt(ch, 36))) rem = (rem * 10 + +d) % 97;
  }
  return rem === 1;
}

// ---------------------------------------------------------------- patterns

const PAN = /^[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z]$/;
const EMAIL = /^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/u;
const TOLL_FREE = /^(?:\+?91[- ]?)?0?18[06]0/;
const PHONE_EXT = /\s*(?:x|ext\.?)\s*\d+$/i;
const PLACEHOLDER = /([xX*0])\1{5,}|EXAMPLE|YOUR_|REPLACE/i;
const TEMPLATE = /^(?:\$\{\w+\}|\$[A-Z_][A-Z0-9_]*|\{\{.*?\}\}|<[^>]+>)$/;
const UPI = /^[\p{L}\p{N}_.-]{2,256}@[A-Za-z][A-Za-z0-9]{1,63}$/u;
const EXPIRY = /^(0[1-9]|1[0-2])\s?[/-]\s?(\d{2}|20\d{2})$/;
const PEM_SRC = String.raw`-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[\s\S]+?-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----`;
const URI_SRC = String.raw`\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|rediss?|amqps?|mssql)://` +
  String.raw`([^\s:/@|;,]*):([^\s@/|;,]+)@[^\s'"<>|;,]*[^\s'"<>|;,.:?!)\]]`;
const PEM_FULL = new RegExp(`^${PEM_SRC}$`);
const URI_FULL = new RegExp(`^${URI_SRC}$`);
const ADO_PASSWORD = /(?:Password|Pwd)=([^;]+);/i;

// Formats specific enough to trust without the model: [label, global pattern].
export const SECRET_PATTERNS = [
  String.raw`\bAKIA[A-Z0-9]{16}\b`,
  String.raw`\bgh[pousr]_[A-Za-z0-9]{36}\b`,
  String.raw`\bgithub_pat_[A-Za-z0-9]{22}_[A-Za-z0-9]{59}\b`,
  String.raw`\bsk-ant-api03-[A-Za-z0-9_-]{80,}`,
  String.raw`\bsk-proj-[A-Za-z0-9_-]{40,}`,
  String.raw`\bxox[bpas]-\d{8,13}-\d{8,13}-[A-Za-z0-9]{20,}\b`,
  String.raw`\bglpat-[A-Za-z0-9_-]{20}\b`,
  String.raw`\bhf_[A-Za-z0-9]{34}\b`,
  String.raw`\b(?:sk|rk)_live_[A-Za-z0-9]{24,}\b`,
  String.raw`\brzp_live_[A-Za-z0-9]{14}\b`,
  String.raw`\bAIza[A-Za-z0-9_-]{35}\b`,
].map((p) => ["API_KEY", new RegExp(p, "g")]).concat([
  ["AUTH_TOKEN", /\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{16,}/g],
  ["PRIVATE_KEY", new RegExp(PEM_SRC, "g")],
  ["CONNECTION_STRING", new RegExp(URI_SRC, "g")],
]);

// ---------------------------------------------------------------- Python string helpers

// Python's \d and str.isdigit() take any script's digits ("१२३"); the checksums need their values.
// Zero code points of the digit blocks used in India (and Arabic, fullwidth); others stay unknown.
const DIGIT_ZEROS = [0x30, 0x660, 0x6f0, 0x966, 0x9e6, 0xa66, 0xae6, 0xb66, 0xbe6, 0xc66, 0xce6, 0xd66, 0xff10];
const asciiDigit = (ch) => {
  const cp = ch.codePointAt(0);
  const zero = DIGIT_ZEROS.find((z) => cp >= z && cp <= z + 9);
  return zero === undefined ? "?" : String(cp - zero);
};
const digitsOf = (v) => (v.match(/\p{Nd}/gu) || []).map(asciiDigit).join("");
const isDigits = (v) => /^\p{Nd}+$/u.test(v);
const isAlpha = (v) => /^\p{L}+$/u.test(v);
export const isSpace = (ch) => /[\s\x1c-\x1f]/u.test(ch) && ch !== "\ufeff";   // Python str.isspace

// ---------------------------------------------------------------- rules per label

function ipValid(raw) {
  const v = raw.replace(/^\[+|\]+$/g, "");
  const v4 = (s) => {
    const parts = s.split(".");
    if (parts.length !== 4) return null;
    const n = parts.map((p) => (/^\d{1,3}$/.test(p) && !(p.length > 1 && p[0] === "0") ? +p : 256));
    return n.every((x) => x <= 255) ? n : null;
  };
  const four = v4(v);
  if (four) return !(four[0] === 127 || four.every((x) => x === 0));
  // IPv6: 8 groups, "::" once, optional embedded IPv4 at the end, optional %zone
  let s = v;
  const zone = s.indexOf("%");
  if (zone >= 0) {
    if (zone === s.length - 1) return false;
    s = s.slice(0, zone);
  }
  if (!/^[0-9A-Fa-f:.]+$/.test(s) || (s.match(/::/g) || []).length > 1) return false;
  let groups = [];
  const tail = s.lastIndexOf(":");
  if (s.includes(".")) {
    const ip4 = v4(s.slice(tail + 1));
    if (!ip4) return false;
    s = `${s.slice(0, tail + 1)}${((ip4[0] << 8) | ip4[1]).toString(16)}:${((ip4[2] << 8) | ip4[3]).toString(16)}`;
  }
  const [head, rest] = s.includes("::") ? s.split("::") : [s, null];
  const parse = (part) => (part === "" ? [] : part.split(":"));
  const h = parse(head);
  const r = rest === null ? [] : parse(rest);
  if ([...h, ...r].some((g) => !/^[0-9A-Fa-f]{1,4}$/.test(g))) return false;
  if (rest === null) {
    if (h.length !== 8) return false;
    groups = h;
  } else {
    if (h.length + r.length > 7) return false;
    groups = [...h, ...Array(8 - h.length - r.length).fill("0"), ...r];
  }
  const nums = groups.map((g) => parseInt(g, 16));
  const zero = nums.every((x) => x === 0);
  const loop = nums.slice(0, 7).every((x) => x === 0) && nums[7] === 1;
  return !(zero || loop);
}

function phoneValid(v) {
  const s = v.trim();
  if (TOLL_FREE.test(s)) return false;
  if (isDigits(s)) {
    if (s.length === 10) return "6789".includes(s[0]) || ("2345".includes(s[0]) && "23456789".includes(s[3]));
    if (s.length === 11) return (s[0] === "0" && "6789".includes(s[1])) || s[0] === "1";
    if (s.length === 12) return s.startsWith("91") && "6789".includes(s[2]);
    return false;
  }
  const n = digitsOf(s.replace(PHONE_EXT, "")).length;
  return n >= 7 && n <= 15;
}

const secretOk = (v) => !PLACEHOLDER.test(v) && !TEMPLATE.test(v.trim());

export const RULES = {
  AADHAAR: (v) => { const d = digitsOf(v); return d.length === 12 && !"01".includes(d[0]) && verhoeffValid(d); },
  CREDIT_CARD: (v) => { const d = digitsOf(v); return d.length >= 13 && d.length <= 19 && luhnValid(d); },
  PAN: (v) => PAN.test(v.toUpperCase()),
  BANK_ACCOUNT: (v) => {
    if (v.length >= 2 && isAlpha(v.slice(0, 2))) return ibanValid(v);
    const d = digitsOf(v).length;
    return isDigits(v.replace(/[ -]/g, "")) && d >= 9 && d <= 18;
  },
  PHONE: phoneValid,
  IP_ADDRESS: ipValid,
  EMAIL: (v) => EMAIL.test(v),
  API_KEY: (v) => v.length >= 16 && !PLACEHOLDER.test(v),
  UPI_ID: (v) => UPI.test(v),
  CVV: (v) => isDigits(v) && (v.length === 3 || v.length === 4),
  CARD_EXPIRY: (v) => EXPIRY.test(v.trim()),
  OTP_PIN: (v) => isDigits(v) && v.length >= 4 && v.length <= 8,
  USERNAME: (v) => [...v].length >= 3 && [...v].length <= 64 && ![...v].some(isSpace),
  AUTH_TOKEN: (v) => v.length >= 20 && secretOk(v),
  PRIVATE_KEY: (v) => PEM_FULL.test(v.trim().replace(/\\n/g, "\n")),
  CONNECTION_STRING: (v) => {
    const m = v.trim().match(URI_FULL) || v.match(ADO_PASSWORD);
    const pw = m && m[m.length - 1];
    return Boolean(pw) && secretOk(pw);
  },
};

export function isValid(label, value) {
  const rule = RULES[label];
  return !rule || rule(value);
}

export const verify = (spans) => spans.filter((sp) => isValid(sp.label, sp.value));

/** Rule matches win over overlapping model spans; an exact model match is kept as is. */
export function rescue(text, spans) {
  let out = [...spans];
  for (const [label, pat] of SECRET_PATTERNS) {
    pat.lastIndex = 0;
    for (const m of text.matchAll(pat)) {
      const start = m.index;
      const end = start + m[0].length;
      if (!isValid(label, m[0])) continue;
      if (out.some((sp) => sp.start === start && sp.end === end && sp.label === label)) continue;
      out = out.filter((sp) => !(sp.start < end && start < sp.end));
      out.push({ start, end, label, value: m[0], score: 1.0, source: "rule" });
    }
  }
  return out.sort((a, b) => a.start - b.start);
}

export const applyRules = (text, spans) => rescue(text, verify(spans));
