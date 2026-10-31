// Runs on the protected sites. Checks pastes, drops and file uploads before the page sees them,
// checks a typed message when it is sent (Enter / Send button) and warns while typing.
"use strict";

(() => {
  // Started in a stale context (the extension was reloaded while this copy was being injected):
  // no extension APIs, so do nothing; the fresh copy protects this tab.
  try {
    if (!chrome.runtime || !chrome.runtime.id || !chrome.storage) return;
  } catch (e) {
    return;
  }
  // Injected twice by the same extension instance (manifest + "protect open tabs"): one copy is enough.
  if (self.__sddContentLoaded) return;
  self.__sddContentLoaded = true;

  const D = self.SDDDecide;
  const UI = self.SDDUI;
  const REPLAY = new WeakSet();          // events we re-dispatch after approval: let them through
  const MAX_FILE = 25_000_000;
  const TYPING_DELAY = 1500;
  const host = location.hostname;
  let disabled = false;
  let pausedUntil = 0;                   // "pause protection" from the popup: everything allowed until then
  const off = () => disabled || Date.now() < pausedUntil;
  let bypass = false;                    // true while we replay an approved send
  let lastEditable = null;               // the composer the user typed in last
  let checking = false;                  // a send check is already showing
  let offUntil = 0;                      // engine down / not set up: step aside (no interception) until then
  const engineOff = () => Date.now() < offUntil;
  let settings = { allowOverride: true, typingWarnings: true };   // engine/settings.js keeps the rest
  let warmed = false;                    // built-in model asked to load (on the first focus in a text box)
  // Chat sites (the built-in list and sites the user added): sends are checked too. Any other site
  // ("protect all websites"): pastes, drops and uploads only, so Enter keeps its normal meaning.
  const LISTED = chrome.runtime.getManifest().content_scripts[0].matches
    .map((m) => m.replace(/^\w+:\/\//, "").replace(/[:/].*$/, ""));
  let chatSite = LISTED.includes(host);

  // ---------------------------------------------------------------- lifecycle
  // After the extension is reloaded or updated, this copy is orphaned (chrome.runtime.id is gone).
  // It must step aside, not swallow events and then fail open.
  const listeners = [];
  const alive = () => { try { return !!chrome.runtime.id; } catch (e) { return false; } };
  function on(type, fn) {
    const wrapped = (e) => {
      if (!alive()) {
        listeners.forEach(([t, f]) => document.removeEventListener(t, f, true));
        return;
      }
      fn(e);
    };
    listeners.push([type, wrapped]);
    document.addEventListener(type, wrapped, true);
  }

  const applySettings = (st) => {
    settings = { ...settings, ...st };
    chatSite = LISTED.includes(host) || (settings.customSites || []).includes(host);
  };
  chrome.storage.local.get({ disabledSites: [], settings: {}, pausedUntil: 0 }).then((st) => {
    disabled = D.siteDisabled(host, st.disabledSites);
    pausedUntil = st.pausedUntil;
    applySettings(st.settings);
  }).catch(() => {});
  chrome.storage.onChanged.addListener((ch) => {
    if (ch.disabledSites) disabled = D.siteDisabled(host, ch.disabledSites.newValue);
    if (ch.pausedUntil) pausedUntil = ch.pausedUntil.newValue || 0;
    if (ch.settings) {
      applySettings(ch.settings.newValue);
      offUntil = 0;                      // mode or model changed: ask again
    }
  });
  const decide = (a, kind) => D.decide(a, kind, { allowOverride: settings.allowOverride !== false });

  // sendMessage throws synchronously (not a rejected promise) once the extension was reloaded
  const send = (msg) => {
    try { return chrome.runtime.sendMessage(msg).catch(() => ({ ok: false, error: "extension_reloaded" })); }
    catch (e) { return Promise.resolve({ ok: false, error: "extension_reloaded" }); }
  };
  const engine = (op, fields) => send({ type: "engine", op, fields }).then((res) => {
    if (res && res.retryMs) offUntil = Date.now() + res.retryMs;
    return res;
  });
  const log = (event) => send({ type: "log", event });
  const labelsOf = (a) => [...new Set(a.findings.map((f) => f.label))];
  const itemsOf = (a, text) => a.findings.map((f) => ({ label: f.label, severity: f.severity,
                                                       preview: D.preview(text.slice(f.start, f.end)) }));

  // ---------------------------------------------------------------- editors and caret

  function editable(el) {
    if (!(el instanceof Element)) return null;
    const t = el.closest("textarea, input, [contenteditable=''], [contenteditable='true'], [contenteditable='plaintext-only']");
    if (!t) return null;
    if (t.tagName === "INPUT" && !/^(text|search|email|url|tel|)$/.test(t.type)) return null;   // not password: logging in is fine
    return t;
  }

  const isField = (t) => t.tagName === "TEXTAREA" || t.tagName === "INPUT";
  const textOf = (t) => (isField(t) ? t.value : t.innerText);

  function saveSel(t) {
    if (isField(t)) return { start: t.selectionStart, end: t.selectionEnd };
    const s = getSelection();
    return s.rangeCount ? s.getRangeAt(0).cloneRange() : null;
  }

  function insert(t, sel, text) {
    t.focus();
    if (isField(t)) {
      if (sel) t.setSelectionRange(sel.start, sel.end);
    } else if (sel) {
      const s = getSelection();
      s.removeAllRanges();
      s.addRange(sel);
    }
    // insertText goes through the editor's own input handling (React, ProseMirror, Quill, ...)
    if (!document.execCommand("insertText", false, text) && isField(t)) {
      t.setRangeText(text, t.selectionStart, t.selectionEnd, "end");
      t.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  function replaceAll(t, text) {
    t.focus();
    if (isField(t)) {
      t.select();
    } else {
      const r = document.createRange();
      r.selectNodeContents(t);
      const s = getSelection();
      s.removeAllRanges();
      s.addRange(r);
    }
    insert(t, null, text);
  }

  on("focusin", (e) => {
    const t = editable(e.composedPath()[0]);
    if (!t) return;
    lastEditable = t;
    if (!warmed && !off()) {          // built-in model: load it while the user is still typing
      warmed = true;
      send({ type: "warmup" });
    }
  });

  // ---------------------------------------------------------------- paste / text drop

  async function checkText(t, text, event) {
    const sel = saveSel(t);
    const spinner = UI.busy(`Checking ${event === "drop" ? "dropped text" : "paste"} for sensitive data…`);
    const res = await engine("scan", { text, source: `browser-${event}`, context: { url: location.href, event } })
      .finally(() => spinner.done());
    if (!res || !res.ok) {                       // fail-open: never lose the user's paste
      insert(t, sel, text);
      log({ kind: event, risk: "unknown", decision: "allowed (engine not reachable)" });
      return;
    }
    const r = res.result;
    const a = r.assessment;
    const d = decide(a, "text");
    if (d.mode === "allow") {
      insert(t, sel, text);
      if (a.risk !== "none") log({ kind: event, risk: a.risk, labels: labelsOf(a), decision: "allowed" });
      return;
    }
    const choice = await UI.ask({ kind: "text", mode: d.mode, choices: d.choices, assessment: a, items: itemsOf(a, text) });
    if (choice === "report") {
      await report(text, r);
      insert(t, sel, text);
    } else if (choice === "original") {
      insert(t, sel, text);
    } else if (choice === "masked") {
      insert(t, sel, r.masked);
    } else {
      t.focus();
    }
    log({ kind: event, risk: a.risk, labels: labelsOf(a), decision: choice });
  }

  const report = (text, r) => engine("feedback", {
    text, predicted: r.spans.map(({ start, end, label, score }) => ({ start, end, label, score })),
    corrected: [], note: "reported as not sensitive from the browser", source: "browser",
  });

  on("paste", (e) => {
    if (off() || bypass || engineOff() || REPLAY.has(e)) return;
    const t = editable(e.composedPath()[0]);
    const text = e.clipboardData ? e.clipboardData.getData("text/plain") : "";
    if (!t || text.trim().length < 4) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    checkText(t, text, "paste");
  });

  // ---------------------------------------------------------------- sending a typed message

  function sendButtonNear(t) {
    const scope = (t && t.closest("form, [role=dialog], main, footer")) || document;
    return [...scope.querySelectorAll("button, [role=button]")].find((b) => b.offsetParent !== null && isSend(b));
  }

  function isSend(el) {
    return D.isSendControl({ aria: el.getAttribute("aria-label"), title: el.getAttribute("title"),
                             tooltip: el.getAttribute("data-tooltip"), testid: el.getAttribute("data-testid") });
  }

  /** Check the composer before a send; replay() performs the original send if approved. */
  async function checkSubmit(t, replay) {
    const text = textOf(t);
    if (!text || text.trim().length < 4) return replay();
    checking = true;
    try {
      const spinner = UI.busy("Checking your message before sending…");
      const res = await engine("scan", { text, source: "browser-submit", context: { url: location.href, event: "submit" } })
        .finally(() => spinner.done());
      if (!res || !res.ok) {
        log({ kind: "send", risk: "unknown", decision: "allowed (engine not reachable)" });
        return replay();
      }
      const r = res.result;
      const a = r.assessment;
      const d = decide(a, "submit");
      if (d.mode === "allow") return replay();
      const choice = await UI.ask({ kind: "submit", mode: d.mode, choices: d.choices, assessment: a, items: itemsOf(a, text) });
      log({ kind: "send", risk: a.risk, labels: labelsOf(a), decision: choice });
      if (choice === "original" || choice === "report") {
        if (choice === "report") await report(text, r);
        replay();
      } else if (choice === "masked") {
        replaceAll(t, r.masked);
        setTimeout(replay, 80);                  // let the editor take the new text first
      } else {
        t.focus();
      }
    } finally {
      checking = false;
    }
  }

  function replayClick(btn) {
    bypass = true;
    try { btn.click(); } finally { bypass = false; }
  }

  function replayEnter(t, orig) {
    const btn = sendButtonNear(t);
    if (btn && !btn.disabled && btn.getAttribute("aria-disabled") !== "true") return replayClick(btn);
    const ev = new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true, cancelable: true,
                                              ctrlKey: orig.ctrlKey, metaKey: orig.metaKey });
    for (const k of ["keyCode", "which"]) Object.defineProperty(ev, k, { get: () => 13 });   // older editors read these
    bypass = true;
    try { t.dispatchEvent(ev); } finally { bypass = false; }
  }

  on("keydown", (e) => {
    if (!chatSite || off() || bypass || engineOff() || REPLAY.has(e) || !D.isSubmitKey(e, host)) return;
    const t = editable(e.composedPath()[0]);
    if (!t || t.tagName === "INPUT") return;      // single-line inputs: search boxes, subjects
    e.preventDefault();
    e.stopImmediatePropagation();
    if (checking) return;
    const keys = { ctrlKey: e.ctrlKey, metaKey: e.metaKey };
    checkSubmit(t, () => replayEnter(t, keys));
  });

  on("click", (e) => {
    if (!chatSite || off() || bypass || checking || engineOff()) return;
    const btn = e.composedPath()[0] instanceof Element ? e.composedPath()[0].closest("button, [role=button]") : null;
    if (!btn || !isSend(btn)) return;
    const t = lastEditable && lastEditable.isConnected ? lastEditable : editable(document.activeElement);
    if (!t || !textOf(t).trim()) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    checkSubmit(t, () => replayClick(btn));
  });

  // ---------------------------------------------------------------- files

  function readB64(file) {
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(String(fr.result).split(",", 2)[1] || "");
      fr.onerror = () => reject(fr.error);
      fr.readAsDataURL(file);
    });
  }

  /** true = let the upload go ahead */
  async function checkFiles(files, event) {
    const results = [];
    const spinner = UI.busy("Checking file for sensitive data…");
    try {
      for (const [i, f] of files.entries()) {
        if (f.size > MAX_FILE) continue;        // too big to check here: not blocked (fail-open)
        spinner.update(`Checking ${files.length > 1 ? `file ${i + 1} of ${files.length}: ` : ""}${f.name}…`);
        const res = await engine("upload", { name: f.name, data_b64: await readB64(f), source: `browser-${event}`,
                                             context: { url: location.href, event } });
        if (!res || !res.ok) {
          log({ kind: event, risk: "unknown", decision: "allowed (engine not reachable)" });
          return true;
        }
        results.push({ file: f.name, ...res.result });
      }
    } finally {
      spinner.done();
    }
    const worst = D.worst(results);
    const d = decide(worst, "file");
    if (d.mode === "allow") return true;
    const items = results.flatMap((r) => r.findings.map((x) => ({
      label: x.label, severity: x.severity, preview: x.preview,
      where: files.length > 1 ? r.file : (x.location.sheet ? `${x.location.sheet}!${x.location.cell}` : x.location.page ? `p.${x.location.page}` : ""),
    })));
    const choice = await UI.ask({ kind: "file", mode: d.mode, choices: d.choices, assessment: worst, items });
    log({ kind: event, risk: worst.risk, labels: [...new Set(items.map((i) => i.label))], decision: choice });
    return choice === "original" || choice === "report";
  }

  // file pickers: hold the input/change events until the files are checked, then replay them
  const held = new WeakMap();                    // input element -> promise of the verdict
  function onFileEvent(e) {
    const input = e.composedPath()[0];
    if (off() || engineOff() || REPLAY.has(e) || !(input instanceof HTMLInputElement) || input.type !== "file") return;
    if (!input.files || !input.files.length) return;
    e.stopImmediatePropagation();
    if (held.has(input)) return;                 // the twin event (input + change) of the same pick
    const verdict = checkFiles([...input.files], "upload").then((ok) => {
      held.delete(input);
      if (!ok) {
        input.value = "";
        return;
      }
      for (const type of ["input", "change"]) {
        const ev = new Event(type, { bubbles: true });
        REPLAY.add(ev);
        input.dispatchEvent(ev);
      }
    });
    held.set(input, verdict);
  }
  on("input", onFileEvent);
  on("change", onFileEvent);

  on("drop", (e) => {
    if (off() || engineOff() || REPLAY.has(e) || !e.dataTransfer) return;
    const files = [...(e.dataTransfer.files || [])];
    const target = e.composedPath()[0];
    if (files.length) {
      e.preventDefault();
      e.stopImmediatePropagation();
      checkFiles(files, "drop").then((ok) => {
        if (!ok) return;
        const dt = new DataTransfer();
        files.forEach((f) => dt.items.add(f));
        const ev = new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: dt,
                                           clientX: e.clientX, clientY: e.clientY });
        REPLAY.add(ev);
        target.dispatchEvent(ev);
      });
      return;
    }
    const t = editable(target);
    const text = e.dataTransfer.getData("text/plain");
    if (t && text.trim().length >= 4) {
      e.preventDefault();
      e.stopImmediatePropagation();
      checkText(t, text, "drop");
    }
  });

  // ---------------------------------------------------------------- typing (warn only)
  // No in-page note: the count shows on the toolbar icon for this tab, the popup comes on send.

  let typingTimer = null;
  let lastTyped = "";
  const typingBadge = (a) => send({ type: "typing", risk: a ? a.risk : "none", count: a ? a.findings.length : 0 });
  on("input", (e) => {
    if (off() || !chatSite || REPLAY.has(e)) return;
    const t = editable(e.composedPath()[0]);
    if (!t) return;
    lastEditable = t;
    if (engineOff() || settings.typingWarnings === false) return;
    clearTimeout(typingTimer);
    typingTimer = setTimeout(async () => {
      const text = textOf(t);
      if (text === lastTyped) return;
      lastTyped = text;
      if (text.trim().length < 12) return typingBadge(null);
      const res = await engine("scan", { text, source: "browser-typing", audit: "findings",
                                         context: { url: location.href, event: "typing" } });
      if (res && res.ok) {
        const a = res.result.assessment;
        typingBadge(a.risk === "none" || a.action === "allow" ? null : a);
      }
    }, TYPING_DELAY);
  });
})();
