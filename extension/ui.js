// In-page UI: the decision dialog and the "checking" popup, inside a closed shadow root so the
// site's CSS cannot change them and the site's scripts cannot read them.
"use strict";

(function () {
  const COLORS = { critical: "#c62828", high: "#e8590c", medium: "#b7791f", low: "#607d8b", none: "#2e7d32" };
  const CSS = `
    :host { all: initial; }
    .back { position: fixed; inset: 0; background: rgba(15,18,22,.45); z-index: 2147483647;
            display: flex; align-items: center; justify-content: center; font: 14px system-ui, sans-serif; }
    .box { background: #fff; color: #1d2433; width: min(520px, calc(100vw - 32px)); border-radius: 12px;
           box-shadow: 0 12px 40px rgba(0,0,0,.25); padding: 18px 20px; border-top: 5px solid var(--c); }
    h2 { margin: 0 0 6px; font-size: 16px; } p { margin: 4px 0 10px; color: #4a5263; }
    ul { margin: 8px 0 12px; padding: 0; list-style: none; max-height: 220px; overflow: auto; }
    li { display: flex; gap: 8px; align-items: center; padding: 4px 0; border-bottom: 1px solid #eef0f3; }
    .sev { color: #fff; font-size: 10px; font-weight: 700; text-transform: uppercase; border-radius: 4px; padding: 1px 6px; }
    .lab { font-weight: 600; font-size: 12px; } code { margin-left: auto; color: #4a5263; }
    .combo { font-size: 12px; color: #8a1c1c; margin: 2px 0; }
    .btns { display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; margin-top: 8px; }
    button { font: inherit; border-radius: 8px; padding: 7px 14px; border: 1px solid #d0d5dd; background: #fff; cursor: pointer; }
    button.primary { background: var(--c); border-color: var(--c); color: #fff; }
    .report { margin-right: auto; border: 0; background: none; color: #667085; text-decoration: underline; padding: 7px 0; }
    .wait { position: fixed; inset: 0; background: rgba(15,18,22,.25); z-index: 2147483647;
            display: flex; align-items: center; justify-content: center; }
    .busy { display: flex; gap: 10px; align-items: center; background: #fff; color: #1d2433; border-radius: 12px;
            padding: 14px 20px; box-shadow: 0 12px 40px rgba(0,0,0,.25); font: 14px system-ui, sans-serif;
            max-width: min(420px, calc(100vw - 32px)); }
    .spin { width: 14px; height: 14px; flex: none; border-radius: 50%; border: 2px solid #c9d3e6;
            border-top-color: #2f5bd3; animation: spin .8s linear infinite; }
    .secs { color: #667085; font-variant-numeric: tabular-nums; }
    @keyframes spin { to { transform: rotate(360deg); } }
    @media (prefers-reduced-motion: reduce) { .spin { animation-duration: 2.4s; } }
    @media (prefers-color-scheme: dark) {
      .box, .busy { background: #1b2027; color: #e6e9ee; } .spin { border-color: #3a4350; border-top-color: #7aa2ff; } p, code { color: #a9b1bd; }
      li { border-color: #2a313b; } button { background: #232a33; color: #e6e9ee; border-color: #3a4350; }
    }`;
  const LABELS = { original: { text: "Paste anyway", file: "Upload anyway", submit: "Send anyway" },
                   masked: { text: "Paste masked", submit: "Send masked" },
                   cancel: { text: "Cancel", file: "Don't upload", submit: "Edit message" } };

  function mount() {
    const host = document.createElement("datashield-guard");
    const root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `<style>${CSS}</style>`;
    document.documentElement.appendChild(host);
    return { host, root };
  }

  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  /**
   * opts: {kind: "text"|"file", mode, choices, assessment, items: [{label, severity, preview, where?}], title}
   * resolves to "original" | "masked" | "cancel" | "report"
   */
  function ask(opts) {
    return new Promise((resolve) => {
      const { host, root } = mount();
      const a = opts.assessment;
      const c = COLORS[a.risk] || COLORS.medium;
      const verb = { file: "upload", submit: "message", text: "paste" }[opts.kind];
      const title = opts.kind === "submit"
        ? (opts.mode === "block" ? `This message contains ${a.risk} data` : "Sensitive data in this message")
        : opts.mode === "block" ? `Blocked: this ${verb} contains ${a.risk} data` : `Sensitive data in this ${verb}`;
      const detail = opts.kind === "submit"
        ? "Checked on this device before sending; nothing has been sent yet."
        : "Checked on this device; nothing was sent anywhere.";
      const items = opts.items.map((f) =>
        `<li><span class="sev" style="background:${COLORS[f.severity]}">${f.severity}</span>` +
        `<span class="lab">${esc(f.label)}</span>${f.where ? `<span>${esc(f.where)}</span>` : ""}` +
        `<code>${esc(f.preview)}</code></li>`).join("");
      const combos = (a.combos || []).map((x) => `<div class="combo">${esc(x.reason)}</div>`).join("");
      const btns = opts.choices.map((ch, i) =>
        `<button data-c="${ch}" class="${i === 0 ? "primary" : ""}">${(LABELS[ch][opts.kind] || LABELS[ch].text)}</button>`).join("");
      const report = opts.mode !== "block" || opts.kind === "submit"
        ? `<button class="report" data-c="report">Not sensitive? Report</button>` : "";
      const wrap = document.createElement("div");
      wrap.className = "back";
      wrap.innerHTML = `<div class="box" style="--c:${c}" role="dialog" aria-modal="true">
        <h2>${esc(opts.title || title)}</h2><p>${esc(opts.detail || detail)}</p>
        ${combos}<ul>${items}</ul><div class="btns">${report}${btns}</div></div>`;
      root.appendChild(wrap);
      const done = (v) => { host.remove(); document.removeEventListener("keydown", onKey, true); resolve(v); };
      const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done("cancel"); } };
      document.addEventListener("keydown", onKey, true);
      wrap.addEventListener("click", (e) => {
        const b = e.target.closest("button");
        if (b) done(b.dataset.c);
        else if (e.target === wrap) done("cancel");
      });
      const first = wrap.querySelector("button.primary");
      if (first) first.focus();
    });
  }

  /**
   * "Checking ..." popup while the engine works, so a held paste, send or upload does not look stuck.
   * Shown only if the check takes longer than DELAY_MS; returns {update(text), done()}.
   */
  const DELAY_MS = 150;
  function busy(text) {
    let ui = null;
    let label = text;
    const t0 = Date.now();
    const render = () => {
      if (!ui) return;
      const s = Math.floor((Date.now() - t0) / 1000);
      ui.msg.textContent = label;
      ui.secs.textContent = s >= 2 ? `${s}s` : "";
    };
    const show = setTimeout(() => {
      ui = mount();
      const wrap = document.createElement("div");
      wrap.className = "wait";
      wrap.innerHTML = `<div class="busy" role="status"><span class="spin"></span><span class="msg"></span><span class="secs"></span></div>`;
      ui.root.appendChild(wrap);
      const div = wrap.firstElementChild;
      ui.msg = div.querySelector(".msg");
      ui.secs = div.querySelector(".secs");
      ui.tick = setInterval(render, 1000);
      render();
    }, DELAY_MS);
    return {
      update(t) { label = t; render(); },
      done() {
        clearTimeout(show);
        if (ui) { clearInterval(ui.tick); ui.host.remove(); ui = null; }
      },
    };
  }

  self.SDDUI = { ask, busy };
})();
