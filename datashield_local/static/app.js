"use strict";

const COLORS = {
  PERSON: "#2f6fed", EMAIL: "#0e9f6e", PHONE: "#d97706", ADDRESS: "#7c3aed", DATE_OF_BIRTH: "#db2777",
  AADHAAR: "#dc2626", PAN: "#b45309", CREDIT_CARD: "#be123c", BANK_ACCOUNT: "#0369a1",
  IP_ADDRESS: "#0891b2", API_KEY: "#9333ea", PASSWORD: "#e11d48",
  UPI_ID: "#059669", CVV: "#9f1239", CARD_EXPIRY: "#c2410c", OTP_PIN: "#ca8a04",
  USERNAME: "#4f46e5", AUTH_TOKEN: "#7e22ce", PRIVATE_KEY: "#a21caf", CONNECTION_STRING: "#0f766e",
};
const color = (l) => COLORS[l] || "#64748b";

const SAMPLES = [
  ["KYC email", "Hi team, please update KYC for Rahul Verma. PAN ABCPD1234E, Aadhaar 2345 6789 0125, " +
    "mobile +91 98765 43210, email rahul.verma@gmail.com."],
  [".env file", "# .env for prod\nDATABASE_URL=postgres://app:Winter@2024@10.0.3.17:5432/main\n" +
    "OPENAI_API_KEY=sk-proj-AbCdEf1234567890abcdef1234567890ABCD_zyxwvutsrqpo9876543\nLOG_LEVEL=info"],
  ["Payment chat (v2)", "Card 4111 1111 1111 1111 exp 09/27 cvv 123. Or pay to rahul.v@okhdfcbank, " +
    "OTP is 482913. Delivery PIN code 411001.\nPortal login rahul.verma92 / Mumbai@1990\n" +
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.c2lnbmF0dXJlLWJ5dGVzLWhlcmU"],
  ["CSV export", "name,email,phone,city\nPriya Nair,priya.nair@outlook.com,9123456780,Kochi\n" +
    "Arjun Mehta,arjun.m@yahoo.co.in,+91 99887 66554,Pune"],
  ["Clean: shipping log", "Order ORD-2025-004512 shipped via AWB 7845123690. Build 3f9c2ab deployed to prod " +
    "(v2.13.4), request_id=6f1c2d9e-8b7a-4c3d-9e2f-1a2b3c4d5e6f, port 6379."],
  ["Clean: invoice", "Invoice INV/24-25/0456 dated 12/03/2025: 40 units of LED panels, HSN 9405, total Rs. 48,600. " +
    "UPI ref 412345678901. Call our helpline 1800-266-1234."],
  ["Clean: masked values", "Card ending XXXX-XXXX-XXXX-4421 was charged. Password: ********. Aadhaar: XXXX XXXX 7781."],
];

const $ = (id) => document.getElementById(id);
const state = {
  labels: Object.keys(COLORS),
  text: "",          // text the current result belongs to
  predicted: [],     // engine output (never edited)
  current: [],       // reviewer's version
  source: "typed",
  stale: false,
  policy: null,      // severity per label, from /api/info
};

// ------------------------------------------------------------------ helpers

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 2200);
}

const key = (s) => `${s.start}:${s.end}:${s.label}`;
const severity = (label) => state.policy ? (state.policy.labels[label] || state.policy.default) : "";
const sevBadge = (lvl) => lvl ? `<span class="sev sev-${lvl}">${lvl}</span>` : "";
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function maskText(text, spans) {
  let out = "", last = 0;
  for (const s of [...spans].sort((a, b) => a.start - b.start)) {
    if (s.start < last) continue;
    out += text.slice(last, s.start) + `[${s.label}]`;
    last = s.end;
  }
  return out + text.slice(last);
}

function changeSummary() {
  const pred = new Set(state.predicted.map(key));
  const cur = new Set(state.current.map(key));
  const added = state.current.filter((s) => !pred.has(key(s))).length;
  const removed = state.predicted.filter((s) => !cur.has(key(s))).length;
  return { added, removed, changed: added + removed > 0 };
}

// ------------------------------------------------------------------ rendering

function renderResult() {
  const box = $("result");
  if (!state.text) return;
  box.classList.remove("empty");
  const predKeys = new Set(state.predicted.map(key));
  const spans = [...state.current].sort((a, b) => a.start - b.start);
  let html = "", last = 0;
  spans.forEach((s) => {
    if (s.start < last) return;
    html += `<span data-s="${last}">${esc(state.text.slice(last, s.start))}</span>`;
    const cls = ["hl", predKeys.has(key(s)) ? "" : "edited", s.source === "rule" ? "rule" : ""].join(" ");
    html += `<span class="${cls}" data-i="${state.current.indexOf(s)}" style="--c:${color(s.label)}"` +
      ` title="${s.label}${s.score != null ? " · " + s.score.toFixed(2) : ""}${s.source === "rule" ? " · rule" : ""}">` +
      `<span data-s="${s.start}">${esc(state.text.slice(s.start, s.end))}</span><span class="tag">${s.label}</span></span>`;
    last = s.end;
  });
  html += `<span data-s="${last}">${esc(state.text.slice(last))}</span>`;
  box.innerHTML = html;

  // table
  const rows = [...state.current].sort((a, b) => a.start - b.start).map((s) => {
    const i = state.current.indexOf(s);
    return `<tr><td><span class="badge" style="--c:${color(s.label)}">${s.label}</span></td>` +
      `<td>${sevBadge(severity(s.label))}</td><td class="value">${esc(state.text.slice(s.start, s.end))}</td>` +
      `<td class="num">${s.score != null ? s.score.toFixed(2) : "—"}</td>` +
      `<td class="muted">${predKeys.has(key(s)) ? (s.source === "rule" ? "rule" : "model") : "you"}</td>` +
      `<td><button class="x" data-remove="${i}" title="Not sensitive">✕</button></td></tr>`;
  });
  const cur = new Set(state.current.map(key));
  state.predicted.filter((p) => !cur.has(key(p))).forEach((p) => {
    rows.push(`<tr class="removed"><td><span class="badge" style="--c:${color(p.label)}">${p.label}</span></td>` +
      `<td>${sevBadge(severity(p.label))}</td><td class="value">${esc(state.text.slice(p.start, p.end))}</td><td class="num">${p.score != null ? p.score.toFixed(2) : "—"}</td>` +
      `<td class="muted">removed</td><td></td></tr>`);
  });
  $("spanRows").innerHTML = rows.join("") || `<tr><td colspan="6" class="muted">No sensitive data found</td></tr>`;

  $("masked").textContent = maskText(state.text, state.current);
  $("masked").classList.remove("muted");

  const c = changeSummary();
  $("changes").textContent = c.changed ? `your edits: +${c.added} added, −${c.removed} removed` : "";
  $("resetBtn").disabled = !c.changed;
  $("saveBtn").disabled = state.stale;
  $("saveBtn").textContent = c.changed ? "Save corrections" : "Save as correct";
  refreshRisk();
}

// document risk for the reviewer's current spans (server applies configs/policy.yaml)
async function refreshRisk() {
  const seq = (refreshRisk.seq = (refreshRisk.seq || 0) + 1);
  const spans = state.current.map(({ start, end, label }) => ({ start, end, label }));
  let a;
  try {
    a = await api("/api/assess", { method: "POST", body: JSON.stringify({ spans }) });
  } catch (e) {
    return;
  }
  if (seq !== refreshRisk.seq) return;  // a newer edit already asked
  renderRisk(a);
}

function renderRisk(a, box = $("risk")) {
  box.hidden = false;
  box.className = `risk risk-${a.risk}`;
  if (a.risk === "none") {
    box.innerHTML = "<b>No risk found</b> · nothing sensitive in this text";
    return;
  }
  const counts = Object.entries(a.counts).filter(([, n]) => n).map(([l, n]) => `${n} ${l}`).join(" · ");
  const combos = a.combos.map((c) => `<li><b>${c.name.replace(/_/g, " ")}</b> (${c.level}): ${esc(c.reason)}</li>`).join("");
  box.innerHTML = `<div><b>Risk: ${a.risk}</b> · action <b>${a.action}</b> · ${counts}</div>` +
    (combos ? `<ul>${combos}</ul>` : "");
}

function renderLegend() {
  $("legend").innerHTML = state.labels.map((l) => `<span style="--c:${color(l)}"><i></i>${l}</span>`).join("");
}

// ------------------------------------------------------------------ scan

async function scan() {
  const text = $("input").value;
  if (!text.trim()) return toast("Paste or type some text first");
  $("scanBtn").disabled = true;
  $("status").textContent = "Scanning…";
  try {
    const r = await api("/api/scan", {
      method: "POST",
      body: JSON.stringify({ text, thresholds: $("optThr").checked, validators: $("optVal").checked }),
    });
    state.text = text;
    state.predicted = r.spans.map((s) => ({ ...s }));
    state.current = r.spans.map((s) => ({ ...s }));
    state.stale = false;
    $("stale").hidden = true;
    $("status").textContent = `${r.spans.length} found · ${r.ms.toFixed(0)} ms · ${r.chars.toLocaleString()} chars`;
    renderResult();
  } catch (e) {
    $("status").textContent = "Error: " + e.message;
  } finally {
    $("scanBtn").disabled = false;
  }
}

// ------------------------------------------------------------------ file scan

const DOC_EXT = /\.(pdf|docx|xlsx|xlsm|zip|jar|eml|png|jpe?g|gif|bmp|tiff?|webp)$/i;

function locText(loc) {
  const parts = [];
  if (loc.member) parts.push(loc.member);
  if (loc.sheet) parts.push(`${loc.sheet}!${loc.cell || ""}`);
  if (loc.page) parts.push(`page ${loc.page}`);
  if (loc.part) parts.push(loc.part);
  if (loc.line) parts.push(`line ${loc.line}`);
  if (loc.paragraph) parts.push(`paragraph ${loc.paragraph}`);
  return parts.join(", ");
}

async function scanFile(f) {
  if (f.size > 25_000_000) return toast("File is larger than 25 MB");
  $("fileCard").hidden = false;
  $("fileStatus").textContent = `Scanning ${f.name}…`;
  $("fileRows").innerHTML = "";
  $("fileSkipped").textContent = "";
  try {
    const bytes = new Uint8Array(await f.arrayBuffer());
    let bin = "";
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    const r = await api("/api/scan-upload", { method: "POST", body: JSON.stringify({ name: f.name, data_b64: btoa(bin) }) });
    $("fileStatus").textContent = `${f.name} · ${r.findings.length} found · ${(r.ms / 1000).toFixed(1)} s`;
    renderRisk(r, $("fileRisk"));
    $("fileRows").innerHTML = r.findings.map((x) =>
      `<tr><td>${sevBadge(x.severity)}</td><td><span class="badge" style="--c:${color(x.label)}">${x.label}</span></td>` +
      `<td>${esc(locText(x.location))}</td><td class="value">${esc(x.preview)}</td></tr>`).join("") ||
      `<tr><td colspan="4" class="muted">No sensitive data found</td></tr>`;
    $("fileSkipped").textContent = r.skipped.map((s) => `skipped ${s.member}: ${s.reason}`).join(" · ");
    $("fileCard").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    $("fileStatus").textContent = "Error: " + err.message;
  }
}

// ------------------------------------------------------------------ editing

function hidePops() {
  $("spanPop").hidden = true;
  $("addPop").hidden = true;
}

function placePop(pop, rect) {
  pop.hidden = false;
  const w = pop.offsetWidth;
  const left = Math.min(window.scrollX + rect.left, window.scrollX + document.documentElement.clientWidth - w - 12);
  pop.style.left = Math.max(8, left) + "px";
  pop.style.top = window.scrollY + rect.bottom + 6 + "px";
}

function labelButtons(container, currentLabel, onPick) {
  container.innerHTML = state.labels.map((l) =>
    `<button data-l="${l}" class="${l === currentLabel ? "current" : ""}" style="--c:${color(l)}"><i></i>${l}</button>`).join("");
  container.onclick = (e) => {
    const b = e.target.closest("button[data-l]");
    if (b) onPick(b.dataset.l);
  };
}

function openSpanPop(i, el) {
  const s = state.current[i];
  labelButtons($("spanPopLabels"), s.label, (l) => {
    state.current[i] = { ...s, label: l, score: l === s.label ? s.score : null, source: l === s.label ? s.source : "user" };
    hidePops();
    renderResult();
  });
  $("spanPopRemove").onclick = () => {
    state.current.splice(i, 1);
    hidePops();
    renderResult();
  };
  placePop($("spanPop"), el.getBoundingClientRect());
}

// map a DOM selection inside #result to character offsets of state.text
function selectionOffsets() {
  const sel = window.getSelection();
  if (!sel.rangeCount || sel.isCollapsed) return null;
  const r = sel.getRangeAt(0);
  const box = $("result");
  if (!box.contains(r.startContainer) || !box.contains(r.endContainer)) return null;
  const off = (node, o) => {
    const seg = (node.nodeType === 3 ? node.parentElement : node).closest("[data-s]");
    if (!seg) return null;
    return node.nodeType === 3 ? +seg.dataset.s + o : +seg.dataset.s;
  };
  let a = off(r.startContainer, r.startOffset), b = off(r.endContainer, r.endOffset);
  if (a == null || b == null) return null;
  if (a > b) [a, b] = [b, a];
  while (a < b && /\s/.test(state.text[a])) a++;
  while (b > a && /\s/.test(state.text[b - 1])) b--;
  return a < b ? { start: a, end: b, rect: r.getBoundingClientRect() } : null;
}

function openAddPop(sel) {
  $("addPopValue").textContent = state.text.slice(sel.start, sel.end).slice(0, 60);
  labelButtons($("addPopLabels"), null, (l) => {
    state.current = state.current.filter((s) => !(s.start < sel.end && sel.start < s.end));
    state.current.push({ start: sel.start, end: sel.end, label: l, score: null, source: "user" });
    window.getSelection().removeAllRanges();
    hidePops();
    renderResult();
  });
  placePop($("addPop"), sel.rect);
}

// ------------------------------------------------------------------ feedback

async function saveReview() {
  if (state.stale || !state.text) return;
  try {
    const r = await api("/api/feedback", {
      method: "POST",
      body: JSON.stringify({ text: state.text, predicted: state.predicted, corrected: state.current,
        note: $("note").value, source: state.source }),
    });
    const d = r.diff;
    toast(`Saved review #${r.id}: ${d.correct} correct, ${d.missed} missed, ${d.wrong} false alarms`);
    $("note").value = "";
    state.predicted = state.current.map((s) => ({ ...s }));
    renderResult();
    loadFeedback();
  } catch (e) {
    toast("Save failed: " + e.message);
  }
}

const pct = (x) => (x * 100).toFixed(1) + "%";

async function loadFeedback() {
  const { stats, recent } = await api("/api/feedback");
  $("fbCount").textContent = stats.docs || "";
  const t = stats.totals;
  const tile = (k, v, hint = "") => `<div class="tile" title="${hint}"><div class="k">${k}</div><div class="v">${v}</div></div>`;
  $("tiles").innerHTML = [
    tile("Reviewed docs", stats.docs),
    tile("Clean docs", stats.clean_docs, "documents you confirmed contain nothing sensitive"),
    tile("Precision", stats.docs ? pct(stats.precision) : "—", "exact matches / everything the detector flagged"),
    tile("Recall", stats.docs ? pct(stats.recall) : "—", "exact matches / everything that should be flagged"),
    tile("Missed", t.missed || 0, "you added these"),
    tile("False alarms", t.wrong || 0, "you removed these"),
  ].join("");

  const bl = stats.by_label;
  $("labelRows").innerHTML = state.labels.map((l) => {
    const n = (k) => (bl[k] && bl[k][l]) || 0;
    if (!n("correct") && !n("missed") && !n("wrong") && !n("boundary")) return "";
    return `<tr><td><span class="badge" style="--c:${color(l)}">${l}</span></td><td class="num">${n("correct")}</td>` +
      `<td class="num">${n("missed")}</td><td class="num">${n("wrong")}</td><td class="num">${n("boundary")}</td></tr>`;
  }).join("") || `<tr><td colspan="5" class="muted">No reviews yet</td></tr>`;
  const rel = Object.entries(bl.relabel || {});
  $("relabels").textContent = rel.length ? "Type changes: " + rel.map(([k, v]) => `${k} ×${v}`).join(", ") : "";

  $("recent").innerHTML = recent.map((r) => {
    const c = r.counts;
    const when = new Date(r.created_at * 1000).toLocaleString();
    return `<li><div class="pv">${esc(r.preview)}</div><button class="x" data-del="${r.id}" title="Delete this review">✕</button>` +
      `<div class="meta">#${r.id} · ${when} · ${esc(r.source || "")} · <b>${c.correct}</b> correct · <b>${c.missed}</b> missed · ` +
      `<b>${c.wrong}</b> false alarms${c.relabel ? ` · <b>${c.relabel}</b> relabels` : ""}${c.boundary ? ` · <b>${c.boundary}</b> boundary` : ""}` +
      `${r.note ? " · “" + esc(r.note) + "”" : ""}</div></li>`;
  }).join("") || `<li class="muted">Saved reviews show up here.</li>`;
}

// ------------------------------------------------------------------ wiring

function init() {
  $("samples").innerHTML += SAMPLES.map(([n], i) => `<option value="${i}">${n}</option>`).join("");
  $("samples").onchange = (e) => {
    if (e.target.value === "") return;
    $("input").value = SAMPLES[+e.target.value][1];
    state.source = "sample";
    e.target.value = "";
    onInput();
    scan();
  };
  $("openFile").onclick = () => $("fileInput").click();
  $("fileInput").onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    e.target.value = "";
    // documents, archives and big files go to the engine's file scanner (locations per finding);
    // small text files open in the editor for review and feedback
    if (DOC_EXT.test(f.name) || f.size > 2_000_000) return scanFile(f);
    $("input").value = await f.text();
    state.source = "file:" + f.name;
    e.target.value = "";
    onInput();
    scan();
  };
  const onInput = () => {
    $("charCount").textContent = $("input").value.length.toLocaleString() + " chars";
    if (state.text && $("input").value !== state.text) {
      state.stale = true;
      $("stale").hidden = false;
      $("saveBtn").disabled = true;
    } else if (state.text) {
      state.stale = false;
      $("stale").hidden = true;
      renderResult();
    }
  };
  $("input").addEventListener("input", () => { state.source = "typed"; onInput(); });
  $("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); scan(); }
  });
  $("scanBtn").onclick = scan;
  $("saveBtn").onclick = saveReview;
  $("resetBtn").onclick = () => { state.current = state.predicted.map((s) => ({ ...s })); renderResult(); };
  $("copyMasked").onclick = () => navigator.clipboard.writeText($("masked").textContent).then(() => toast("Masked text copied"));

  $("result").addEventListener("click", (e) => {
    const hl = e.target.closest(".hl");
    if (hl && window.getSelection().isCollapsed) { e.stopPropagation(); openSpanPop(+hl.dataset.i, hl); }
  });
  $("result").addEventListener("mouseup", () => {
    setTimeout(() => { const s = selectionOffsets(); if (s) openAddPop(s); }, 0);
  });
  $("spanRows").addEventListener("click", (e) => {
    const b = e.target.closest("[data-remove]");
    if (b) { state.current.splice(+b.dataset.remove, 1); renderResult(); }
  });
  $("recent").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-del]");
    if (!b) return;
    await api(`/api/feedback/${b.dataset.del}`, { method: "DELETE" });
    loadFeedback();
  });
  document.addEventListener("mousedown", (e) => {
    if (!e.target.closest(".pop") && !e.target.closest(".hl")) hidePops();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hidePops(); });

  document.querySelectorAll(".tab").forEach((t) => t.onclick = () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
    document.querySelectorAll(".tab-panel").forEach((p) => (p.hidden = p.id !== "tab-" + t.dataset.tab));
    if (t.dataset.tab === "feedback") loadFeedback();
  });

  api("/api/info").then((i) => {
    state.labels = i.labels;
    state.policy = i.policy;
    $("modelInfo").textContent = `${i.model_id} · ${i.device}`;
    $("dbPath").textContent = "Feedback database: " + i.feedback_db;
    renderLegend();
  }).catch(() => ($("modelInfo").textContent = "engine not reachable"));
  renderLegend();
  loadFeedback().catch(() => {});
}

init();
