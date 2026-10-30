// Service worker: the only part of the extension that talks to the engine (via the native host).
"use strict";

const HOST = "com.datashield.engine";
const TIMEOUT_MS = { health: 5000, scan: 60000, upload: 300000, feedback: 30000 };
const MAX_EVENTS = 50;
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

async function call(op, fields = {}) {
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

async function setBadge(ok) {
  await chrome.action.setBadgeText({ text: ok ? "" : "!" });
  if (!ok) await chrome.action.setBadgeBackgroundColor({ color: "#c62828" });
  await chrome.storage.session.set({ engineOk: ok });
}

// Events keep labels and decisions only, never the text or values.
async function logEvent(ev) {
  const { events = [] } = await chrome.storage.local.get("events");
  events.unshift({ ts: Date.now(), ...ev });
  await chrome.storage.local.set({ events: events.slice(0, MAX_EVENTS) });
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    if (msg.type === "engine") {
      const res = await call(msg.op, msg.fields);
      if (msg.op !== "feedback" && res.error !== "engine_off") await setBadge(res.ok);
      reply(res);
    } else if (msg.type === "log") {
      await logEvent({ host: sender.tab ? new URL(sender.tab.url).hostname : "", ...msg.event });
      reply({ ok: true });
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

chrome.runtime.onStartup.addListener(async () => setBadge((await call("health")).ok));
chrome.runtime.onInstalled.addListener(async () => {
  await protectOpenTabs();
  await setBadge((await call("health")).ok);
});
