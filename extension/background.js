// Service worker: every check from the content script comes here and goes to the detection mode
// the user chose (settings.mode):
//   builtin  the model inside the extension, run in an offscreen page (engine/, offscreen.js)
//   engine   the desktop engine, through the native-messaging host (datashield-local)
// Either way the result is assessed with the user's policy (engine/results.js), so the settings
// page decides what is blocked, masked or only warned about.
import { Policy } from "./engine/policy.js";
import { fileResult, reassessFile, reassessText, textResult } from "./engine/results.js";
import { ALL_SITES_ID, getSettings, saveSettings } from "./engine/settings.js";

const HOST = "com.datashield.engine";
const TIMEOUT_MS = { health: 5000, scan: 60000, upload: 300000, feedback: 30000 };
const MAX_EVENTS = 50;

// ---------------------------------------------------------------- policy

let policyCfg = null;
async function userPolicy(settings) {
  policyCfg ??= await (await fetch(chrome.runtime.getURL("engine/policy.json"))).json();
  return new Policy(policyCfg, { labelActions: settings.labelActions });
}

// ---------------------------------------------------------------- desktop engine (native host)

// Engine down: answer "engine_off" at once instead of asking the host on every keystroke or send,
// and look again after a pause that grows while the engine stays down.
const DOWN_ERRORS = new Set(["engine_down", "host_unavailable", "host_closed"]);
const RETRY_MS = { first: 15000, max: 120000 };
let offUntil = 0;
let retryMs = RETRY_MS.first;

let port = null;
let nextId = 1;
const pending = new Map();   // id -> {resolve, timer}

function connect() {
  if (port) return port;
  try {
    port = chrome.runtime.connectNative(HOST);
  } catch (e) {
    port = null;
    return null;
  }
  port.onMessage.addListener((msg) => {
    const p = pending.get(msg.id);
    if (!p) return;
    clearTimeout(p.timer);
    pending.delete(msg.id);
    p.resolve(msg);
  });
  port.onDisconnect.addListener(() => {
    // host not installed, crashed, or the worker was idle: fail every waiting request
    const err = chrome.runtime.lastError ? "host_unavailable" : "host_closed";
    for (const [id, p] of pending) {
      clearTimeout(p.timer);
      p.resolve({ id, ok: false, error: err });
    }
    pending.clear();
    port = null;
  });
  return port;
}

function rawCall(op, fields) {
  return new Promise((resolve) => {
    const p = connect();
    if (!p) return resolve({ ok: false, error: "host_unavailable" });
    const id = nextId++;
    const timer = setTimeout(() => {
      pending.delete(id);
      resolve({ id, ok: false, error: "timeout" });
    }, TIMEOUT_MS[op] || 60000);
    pending.set(id, { resolve, timer });
    p.postMessage({ id, op, ...fields });
  });
}

async function engineCall(op, fields = {}) {
  if (op !== "health" && Date.now() < offUntil) {
    return { ok: false, error: "engine_off", retryMs: offUntil - Date.now() };
  }
  const res = await rawCall(op, fields);
  if (res.ok) {
    offUntil = 0;
    retryMs = RETRY_MS.first;
  } else if (DOWN_ERRORS.has(res.error)) {
    offUntil = Date.now() + retryMs;
    res.retryMs = retryMs;
    retryMs = Math.min(retryMs * 2, RETRY_MS.max);
  }
  return res;
}

async function viaEngine(op, fields, settings) {
  const res = await engineCall(op, fields);
  if (!res.ok || !res.result) return res;
  const policy = await userPolicy(settings);
  if (op === "scan") return { ...res, result: reassessText(res.result, policy) };
  if (op === "upload") return { ...res, result: reassessFile(res.result, policy) };
  return res;
}

// ---------------------------------------------------------------- built-in model (offscreen page)

let creating = null;
async function ensureOffscreen() {
  if (await chrome.offscreen.hasDocument()) return;
  creating ??= chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["WORKERS"],
    justification: "Runs the DataShield detection model on this device to check text and files before they are sent.",
  }).finally(() => { creating = null; });
  await creating;
}

async function offscreen(msg) {
  await ensureOffscreen();
  try {
    return await chrome.runtime.sendMessage({ target: "offscreen", ...msg });
  } catch (e) {
    return { ok: false, error: "detector_unavailable", message: String(e.message || e) };
  }
}

async function viaBuiltin(op, fields, settings) {
  if (op === "health" || op === "warmup") {
    const r = await offscreen({ op: "warmup" });
    return r.ok ? { ok: true, result: { status: "ok", mode: "builtin" } } : r;
  }
  if (op === "feedback") return { ok: true, result: { stored: false } };   // no text leaves the page in built-in mode
  const policy = await userPolicy(settings);
  if (op === "scan") {
    const r = await offscreen({ op: "text", text: fields.text });
    return r.ok ? { ok: true, result: textResult(fields.text, r.result.spans, r.result.suppressed, policy, r.result.ms) } : r;
  }
  if (op === "upload") {
    const r = await offscreen({ op: "file", name: fields.name, data_b64: fields.data_b64 });
    return r.ok ? { ok: true, result: fileResult(fields.name, r.result.size, r.result, policy, r.result.ms) } : r;
  }
  return { ok: false, error: "unknown_op" };
}

// ---------------------------------------------------------------- routing, badge, events

// Not usable for a while (no mode chosen yet, model not downloaded): the content script steps
// aside for this long instead of asking on every keystroke.
const SETUP_RETRY_MS = 60000;

async function check(op, fields = {}) {
  const settings = await getSettings();
  const res = !settings.mode ? { ok: false, error: "not_set_up" }
    : settings.mode === "engine" ? await viaEngine(op, fields, settings) : await viaBuiltin(op, fields, settings);
  if (["not_set_up", "model_missing"].includes(res.error)) res.retryMs = SETUP_RETRY_MS;
  return res;
}

async function setBadge(ok) {
  await chrome.action.setBadgeText({ text: ok ? "" : "!" });
  if (!ok) await chrome.action.setBadgeBackgroundColor({ color: "#c62828" });
  await chrome.storage.session.set({ engineOk: ok });
}

// Events keep labels and decisions only, never the text or values. stats counts what was found
// (and how often the user kept it out) since install or the last "clear", for the popup.
async function logEvent(ev) {
  const { events = [], stats = { found: 0, kept: 0, since: Date.now() } } = await chrome.storage.local.get(["events", "stats"]);
  events.unshift({ ts: Date.now(), ...ev });
  if (ev.risk && !["none", "unknown"].includes(ev.risk)) {
    stats.found += 1;
    if (["masked", "cancel"].includes(ev.decision)) stats.kept += 1;
  }
  await chrome.storage.local.set({ events: events.slice(0, MAX_EVENTS), stats });
}

// Typing warnings: the number of findings in the draft, on this tab's toolbar icon (no in-page note).
const RISK_COLORS = { critical: "#c62828", high: "#e8590c", medium: "#b7791f", low: "#607d8b" };
async function typingBadge(tabId, risk, count) {
  if (tabId == null) return;
  const show = count > 0 && RISK_COLORS[risk];
  await chrome.action.setBadgeText({ tabId, text: show ? String(Math.min(count, 99)) : "" }).catch(() => {});
  if (show) await chrome.action.setBadgeBackgroundColor({ tabId, color: RISK_COLORS[risk] }).catch(() => {});
  await chrome.action.setTitle({ tabId, title: show ? `DataShield Local: ${count} sensitive item(s) in your draft (${risk})`
                                                    : "DataShield Local" }).catch(() => {});
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg?.target === "offscreen") return false;          // for the offscreen page, not for us
  (async () => {
    if (msg.type === "engine") {
      const res = await check(msg.op, msg.fields);
      if (msg.op !== "feedback" && res.error !== "engine_off") await setBadge(res.ok);
      reply(res);
    } else if (msg.type === "warmup") {
      const { mode } = await getSettings();
      if (mode === "builtin") check("warmup").catch(() => {});
      reply({ ok: true });
    } else if (msg.type === "typing") {
      await typingBadge(sender.tab?.id, msg.risk, msg.count);
      reply({ ok: true });
    } else if (msg.type === "log") {
      await logEvent({ host: sender.tab ? new URL(sender.tab.url).hostname : "", ...msg.event });
      reply({ ok: true });
    } else if (msg.type === "offscreen-idle") {
      if (await chrome.offscreen.hasDocument()) await chrome.offscreen.closeDocument();
      reply({ ok: true });
    } else if (msg.type === "status") {
      reply(await check("health"));
    } else if (msg.type === "engine-health") {           // settings page: is the desktop engine there?
      reply(await engineCall("health"));
    }
  })();
  return true;   // async reply
});

// Chrome only adds content scripts to pages loaded after install. Tabs already open on a protected
// site would stay unprotected until reloaded, so add the scripts to them now. (An older copy left
// in a tab by an update steps aside on its own: see "lifecycle" in content.js.)
async function protectOpenTabs() {
  const cs = chrome.runtime.getManifest().content_scripts[0];
  const tabs = await chrome.tabs.query({ url: cs.matches });
  await Promise.all(tabs.map((t) =>
    chrome.scripting.executeScript({ target: { tabId: t.id }, files: cs.js }).catch(() => {})));
}

// "Protect all websites" lasts only while the browser grants access to all sites: if the user takes
// that back (chrome://extensions → Site access), switch the setting off and drop the script.
chrome.permissions.onRemoved.addListener(async ({ origins = [] }) => {
  if (!origins.some((o) => o.startsWith("https://*/") || o.startsWith("http://*/"))) return;
  await chrome.scripting.unregisterContentScripts({ ids: [ALL_SITES_ID] }).catch(() => {});
  await saveSettings({ allSites: false });
});

chrome.runtime.onStartup.addListener(async () => {
  const { mode } = await getSettings();
  if (mode === "engine") await setBadge((await engineCall("health")).ok);
});
chrome.runtime.onInstalled.addListener(async ({ reason }) => {
  await protectOpenTabs();
  const { mode } = await getSettings();
  if (!mode) {
    await setBadge(false);
    if (reason === "install" || reason === "update") chrome.runtime.openOptionsPage();
  } else if (mode === "engine") {
    await setBadge((await engineCall("health")).ok);
  }
});
