// Toolbar popup: detection status, this site on/off, pause, counts and recent events (labels only).
import * as store from "./engine/modelstore.js";
import { MODES, getSettings } from "./engine/settings.js";

const COLORS = { critical: "#c62828", high: "#e8590c", medium: "#b7791f", low: "#607d8b", unknown: "#667085" };
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function main() {
  $("settings").onclick = (e) => {
    e.preventDefault();
    chrome.runtime.openOptionsPage();
  };
  const show = (ok, text) => {
    $("dot").style.background = ok ? "var(--ok)" : "var(--bad)";
    $("status").textContent = text;
  };
  const { mode } = await getSettings();
  if (!mode) {
    show(false, "not set up: open settings to choose how to check");
  } else if (mode === "builtin") {
    // the download state only: opening the popup should not load the model into memory
    const s = await store.status();
    show(s.installed, s.installed ? `${MODES.builtin} · ready` : "built-in model not downloaded: open settings");
  } else {
    const health = await chrome.runtime.sendMessage({ type: "engine", op: "health" });
    show(health.ok, health.ok ? `${MODES.engine} · ${health.result.model_id}`
                              : "desktop engine not running: nothing is checked");
  }

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const host = tab && tab.url ? new URL(tab.url).hostname : "";
  $("site").textContent = host || "this site";
  // is this tab covered at all? (listed site, a site the user added, or "protect all websites")
  const listed = chrome.runtime.getManifest().content_scripts[0].matches
    .map((m) => m.replace(/^\w+:\/\//, "").replace(/[:/].*$/, ""));
  const { customSites, allSites } = await getSettings();
  const web = /^https?:/.test(tab?.url || "");
  const covered = listed.includes(host) || customSites.includes(host) ||
    (allSites && web && await chrome.permissions.contains({ origins: ["https://*/*", "http://*/*"] }));
  $("not-covered").hidden = covered || !web;
  $("all-sites").onclick = (e) => {
    e.preventDefault();
    chrome.runtime.openOptionsPage();
  };
  const { disabledSites = [] } = await chrome.storage.local.get("disabledSites");
  $("toggle").checked = !disabledSites.includes(host);
  $("toggle").disabled = !host || !covered;
  $("toggle").onchange = async (e) => {
    const { disabledSites: cur = [] } = await chrome.storage.local.get("disabledSites");
    const next = e.target.checked ? cur.filter((h) => h !== host) : [...new Set([...cur, host])];
    await chrome.storage.local.set({ disabledSites: next });
  };

  // pause: everything is allowed until the time is up (content.js reads pausedUntil)
  const renderPause = async () => {
    const { pausedUntil = 0 } = await chrome.storage.local.get("pausedUntil");
    const paused = pausedUntil > Date.now();
    $("pause-state").textContent = paused
      ? `Paused until ${new Date(pausedUntil).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "Protection is on";
    $("pause-state").className = paused ? "warn" : "";
    $("resume").hidden = !paused;
  };
  for (const b of document.querySelectorAll("#pause-btns button")) {
    b.onclick = async () => {
      await chrome.storage.local.set({ pausedUntil: Date.now() + Number(b.dataset.min) * 60000 });
      renderPause();
    };
  }
  $("resume").onclick = async () => {
    await chrome.storage.local.set({ pausedUntil: 0 });
    renderPause();
  };
  await renderPause();

  const { events = [], stats } = await chrome.storage.local.get(["events", "stats"]);
  $("found").textContent = stats?.found ?? 0;
  $("kept").textContent = stats?.kept ?? 0;
  $("clear").onclick = async () => {
    await chrome.storage.local.set({ events: [], stats: { found: 0, kept: 0, since: Date.now() } });
    $("events").innerHTML = `<li class="muted">nothing yet</li>`;
    $("found").textContent = $("kept").textContent = "0";
  };
  if (events.length) {
    $("events").innerHTML = events.slice(0, 20).map((e) =>
      `<li><span class="risk" style="color:${COLORS[e.risk] || COLORS.unknown}">${esc(e.risk)}</span> ` +
      `${esc(e.kind)} on ${esc(e.host)} · ${esc((e.labels || []).join(", "))} → <b>${esc(e.decision)}</b>` +
      `<div class="muted">${new Date(e.ts).toLocaleString()}</div></li>`).join("");
  }
}

main();
