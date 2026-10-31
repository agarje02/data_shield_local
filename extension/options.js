// Settings page (also the welcome page after install): detection mode, model download, action per
// label, block override, typing warnings and the list of protected sites.
import * as store from "./engine/modelstore.js";
import { ACTIONS, ALL_SITES_ID, getSettings, saveSettings } from "./engine/settings.js";

const GROUPS = [
  ["Credentials", { PASSWORD: "Passwords", API_KEY: "API keys", AUTH_TOKEN: "Login tokens (JWT, OAuth, session)",
                    PRIVATE_KEY: "Private keys", CONNECTION_STRING: "Database connection strings" }],
  ["Payments", { CREDIT_CARD: "Card numbers", CARD_EXPIRY: "Card expiry dates", CVV: "CVV / security codes",
                 BANK_ACCOUNT: "Bank accounts and IBANs", UPI_ID: "UPI IDs", OTP_PIN: "OTPs and PINs" }],
  ["Indian IDs", { AADHAAR: "Aadhaar numbers", PAN: "PAN numbers" }],
  ["Personal", { PERSON: "Names", EMAIL: "E-mail addresses", PHONE: "Phone numbers", ADDRESS: "Postal addresses",
                 DATE_OF_BIRTH: "Dates of birth", USERNAME: "Usernames", IP_ADDRESS: "IP addresses" }],
];
const ACTION_NAMES = { block: "Block", mask: "Mask", warn: "Warn", allow: "Allow" };
const CUSTOM_ID = "custom-sites";
const $ = (id) => document.getElementById(id);
const mb = (n) => `${Math.round(n / 1e6)} MB`;

let settings;
let policy;

function status(el, text, kind) {
  el.textContent = text;
  el.className = `status ${kind || ""}`;
}

// ---------------------------------------------------------------- detection mode

async function refreshBuiltin() {
  const s = await store.status();
  $("model-size").textContent = mb(s.bytes);
  if (s.installed) {
    status($("builtin-status"), "✓ Model ready on this device", "ok");
    $("download").hidden = true;
    $("remove").hidden = false;
  } else {
    const some = Object.values(s.files).some(Boolean);
    status($("builtin-status"), some ? "Download not finished" : "Model not downloaded yet", settings.mode === "builtin" ? "bad" : "wait");
    $("download").hidden = false;
    $("remove").hidden = true;
  }
  return s;
}

let downloading = false;
async function download() {
  if (downloading) return;
  downloading = true;
  $("download").disabled = true;
  $("progress").hidden = false;
  try {
    await store.download(({ done, total }) => {
      $("bar").style.width = `${(100 * done) / total}%`;
      status($("builtin-status"), `Downloading… ${mb(done)} of ${mb(total)}`, "wait");
    });
    status($("builtin-status"), "✓ Model ready on this device", "ok");
    chrome.runtime.sendMessage({ type: "warmup" }).catch(() => {});
  } catch (e) {
    status($("builtin-status"), `Download failed: ${e.message}. Check the connection and try again.`, "bad");
  } finally {
    downloading = false;
    $("download").disabled = false;
    $("progress").hidden = true;
    await refreshBuiltin();
  }
}

async function checkEngine() {
  status($("engine-status"), "Checking…", "wait");
  const r = await chrome.runtime.sendMessage({ type: "engine-health" }).catch(() => null);
  if (r?.ok) status($("engine-status"), `✓ Connected to the desktop engine (model ${r.result.model_id})`, "ok");
  else if (r?.error === "host_unavailable") status($("engine-status"), "The app is not installed (or not registered with this browser)", "bad");
  else status($("engine-status"), "The app is installed but the engine is not running", "bad");
}

async function setMode(mode) {
  settings = await saveSettings({ mode });
  $("welcome").hidden = true;
  if (mode === "builtin" && !(await refreshBuiltin()).installed) download();
  if (mode === "engine") checkEngine();
}

// ---------------------------------------------------------------- actions per label

function defaultAction(label) {
  return policy.levels[policy.labels[label] ?? policy.default].action;
}

function renderLabels() {
  const box = $("labels");
  box.textContent = "";
  for (const [group, labels] of GROUPS) {
    const h = document.createElement("div");
    h.className = "group";
    h.textContent = group;
    box.append(h);
    for (const [label, name] of Object.entries(labels)) {
      const row = document.createElement("div");
      row.className = "label-row";
      const id = `act-${label}`;
      const lab = document.createElement("label");
      lab.className = "label-name";
      lab.htmlFor = id;
      lab.textContent = name;
      const small = document.createElement("small");
      small.textContent = `${policy.labels[label] ?? policy.default} severity`;
      lab.append(small);
      const sel = document.createElement("select");
      sel.id = id;
      const dflt = defaultAction(label);
      for (const a of ACTIONS) sel.add(new Option(`${ACTION_NAMES[a]}${a === dflt ? " (default)" : ""}`, a));
      sel.value = settings.labelActions[label] ?? dflt;
      sel.classList.toggle("changed", sel.value !== dflt);
      sel.addEventListener("change", async () => {
        const labelActions = { ...settings.labelActions };
        if (sel.value === dflt) delete labelActions[label];
        else labelActions[label] = sel.value;
        settings = await saveSettings({ labelActions });
        sel.classList.toggle("changed", sel.value !== dflt);
      });
      row.append(lab, sel);
      box.append(row);
    }
  }
}

// ---------------------------------------------------------------- sites

const hostOf = (pattern) => pattern.replace(/^\w+:\/\//, "").replace(/\/.*$/, "");

async function syncCustomScripts(hosts) {
  await chrome.scripting.unregisterContentScripts({ ids: [CUSTOM_ID] }).catch(() => {});
  if (!hosts.length) return;
  const cs = chrome.runtime.getManifest().content_scripts[0];
  await chrome.scripting.registerContentScripts([{ id: CUSTOM_ID, matches: hosts.map((h) => `https://${h}/*`),
                                                   js: cs.js, runAt: "document_start", persistAcrossSessions: true }]);
}

const ALL_ORIGINS = ["https://*/*", "http://*/*"];

async function setAllSites(on) {
  const box = $("all-sites");
  const msg = $("all-sites-msg");
  const cs = chrome.runtime.getManifest().content_scripts[0];
  await chrome.scripting.unregisterContentScripts({ ids: [ALL_SITES_ID] }).catch(() => {});
  if (!on) {
    settings = await saveSettings({ allSites: false });
    msg.textContent = "Only the sites below are checked now. Reload open tabs to stop checking elsewhere.";
    return;
  }
  // the browser asks the user to allow access to all sites
  if (!(await chrome.permissions.request({ origins: ALL_ORIGINS }))) {
    box.checked = false;
    msg.textContent = "Not turned on: access to all sites was not allowed.";
    return;
  }
  await chrome.scripting.registerContentScripts([{ id: ALL_SITES_ID, matches: ALL_ORIGINS, excludeMatches: cs.matches,
                                                   js: cs.js, runAt: "document_start", persistAcrossSessions: true }]);
  settings = await saveSettings({ allSites: true });
  // tabs already open get the checks now (a tab that already has them ignores the second copy)
  const tabs = await chrome.tabs.query({ url: ALL_ORIGINS });
  await Promise.all(tabs.map((t) => chrome.scripting.executeScript({ target: { tabId: t.id }, files: cs.js }).catch(() => {})));
  msg.textContent = "✓ Every website is protected, open tabs included.";
}

async function renderSites() {
  const { disabledSites = [] } = await chrome.storage.local.get("disabledSites");
  const builtin = [...new Set(chrome.runtime.getManifest().content_scripts[0].matches.map(hostOf))]
    .filter((h) => !h.startsWith("127.0.0.1"));
  const ul = $("sites");
  ul.textContent = "";
  const row = (host, custom) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "host";
    name.textContent = host;
    const right = document.createElement("span");
    right.className = "right";
    const lab = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = !disabledSites.includes(host);
    box.addEventListener("change", async () => {
      const cur = (await chrome.storage.local.get("disabledSites")).disabledSites || [];
      const next = box.checked ? cur.filter((h) => h !== host) : [...new Set([...cur, host])];
      await chrome.storage.local.set({ disabledSites: next });
    });
    lab.append(box, " Checked");
    right.append(lab);
    if (custom) {
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "link";
      rm.textContent = "Remove";
      rm.addEventListener("click", async () => {
        const customSites = settings.customSites.filter((h) => h !== host);
        settings = await saveSettings({ customSites });
        await syncCustomScripts(customSites);
        await chrome.permissions.remove({ origins: [`https://${host}/*`] }).catch(() => {});
        renderSites();
      });
      right.append(rm);
    }
    li.append(name, right);
    ul.append(li);
  };
  builtin.forEach((h) => row(h, false));
  settings.customSites.forEach((h) => row(h, true));
}

async function addSite(e) {
  e.preventDefault();
  const raw = $("site-input").value.trim().toLowerCase();
  const host = raw.replace(/^\w+:\/\//, "").replace(/\/.*$/, "");
  if (!/^(?=.{4,253}$)([a-z0-9-]+\.)+[a-z]{2,}$/.test(host)) {
    $("site-msg").textContent = "Enter a site name like chat.example.com";
    return;
  }
  if (settings.customSites.includes(host)) {
    $("site-msg").textContent = `${host} is already on the list.`;
    return;
  }
  // the browser asks the user to allow access to this one site
  const granted = await chrome.permissions.request({ origins: [`https://${host}/*`] });
  if (!granted) {
    $("site-msg").textContent = "Not added: access to the site was not allowed.";
    return;
  }
  const customSites = [...settings.customSites, host];
  settings = await saveSettings({ customSites });
  await syncCustomScripts(customSites);
  $("site-input").value = "";
  $("site-msg").textContent = `${host} added. Reload its open tabs to start checking there.`;
  renderSites();
}

// ---------------------------------------------------------------- start

async function main() {
  settings = await getSettings();
  policy = await (await fetch("engine/policy.json")).json();
  $("version").textContent = `version ${chrome.runtime.getManifest().version}`;
  $("welcome").hidden = Boolean(settings.mode);
  for (const r of document.querySelectorAll("input[name=mode]")) {
    r.checked = r.value === settings.mode;
    r.addEventListener("change", () => r.checked && setMode(r.value));
  }
  $("download").addEventListener("click", (e) => { e.preventDefault(); download(); });
  $("remove").addEventListener("click", async (e) => {
    e.preventDefault();
    await store.remove();
    await refreshBuiltin();
  });
  $("engine-test").addEventListener("click", (e) => { e.preventDefault(); checkEngine(); });
  $("allow-override").checked = settings.allowOverride;
  $("allow-override").addEventListener("change", async (e) => { settings = await saveSettings({ allowOverride: e.target.checked }); });
  $("typing").checked = settings.typingWarnings;
  $("typing").addEventListener("change", async (e) => { settings = await saveSettings({ typingWarnings: e.target.checked }); });
  $("reset-labels").addEventListener("click", async () => {
    settings = await saveSettings({ labelActions: {} });
    renderLabels();
  });
  $("add-site").addEventListener("submit", addSite);
  // on only while the browser still grants access to all sites
  $("all-sites").checked = settings.allSites && await chrome.permissions.contains({ origins: ALL_ORIGINS });
  $("all-sites").addEventListener("change", (e) => setAllSites(e.target.checked));
  renderLabels();
  renderSites();
  await refreshBuiltin();
  if (settings.mode === "engine") checkEngine();
}

main();
