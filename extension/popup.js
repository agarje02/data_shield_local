"use strict";

const COLORS = { critical: "#c62828", high: "#e8590c", medium: "#b7791f", low: "#607d8b", unknown: "#667085" };
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function main() {
  const health = await chrome.runtime.sendMessage({ type: "engine", op: "health" });
  $("dot").style.background = health.ok ? "var(--ok)" : "var(--bad)";
  $("status").textContent = health.ok
    ? `engine running · ${health.result.model_id}`
    : "engine not running: pastes and uploads are allowed unchecked";
  $("open").onclick = () => chrome.tabs.create({ url: "http://127.0.0.1:8765/" });

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const host = tab && tab.url ? new URL(tab.url).hostname : "";
  $("site").textContent = host || "this site";
  const { disabledSites = [] } = await chrome.storage.local.get("disabledSites");
  $("toggle").checked = !disabledSites.includes(host);
  $("toggle").disabled = !host;
  $("toggle").onchange = async (e) => {
    const { disabledSites: cur = [] } = await chrome.storage.local.get("disabledSites");
    const next = e.target.checked ? cur.filter((h) => h !== host) : [...new Set([...cur, host])];
    await chrome.storage.local.set({ disabledSites: next });
  };

  const { events = [] } = await chrome.storage.local.get("events");
  if (events.length) {
    $("events").innerHTML = events.slice(0, 20).map((e) =>
      `<li><span class="risk" style="color:${COLORS[e.risk] || COLORS.unknown}">${esc(e.risk)}</span> ` +
      `${esc(e.kind)} on ${esc(e.host)} · ${esc((e.labels || []).join(", "))} → <b>${esc(e.decision)}</b>` +
      `<div class="muted">${new Date(e.ts).toLocaleString()}</div></li>`).join("");
  }
}

main();
