// User settings (chrome.storage.local "settings"), shared by the background worker, the settings
// page and the popup. The content script reads the same key directly.

export const MODES = { builtin: "Built-in model", engine: "Desktop engine" };
export const ACTIONS = ["block", "mask", "warn", "allow"];
export const ALL_SITES_ID = "all-sites";   // id of the content script registered for "protect all websites"

export const DEFAULTS = {
  mode: null,                 // null until the user picks one on the welcome page
  labelActions: {},           // LABEL -> block | mask | warn | allow; missing = the policy default
  allowOverride: true,        // "Send / Paste / Upload anyway" on blocked items
  typingWarnings: true,       // check while typing (count on the toolbar icon), not only on send
  customSites: [],            // extra hosts the user added (content scripts registered at run time)
  allSites: false,            // also every other website: pastes, drops and uploads (not sends)
};

export async function getSettings() {
  const { settings } = await chrome.storage.local.get("settings");
  return { ...DEFAULTS, ...(settings || {}) };
}

export async function saveSettings(patch) {
  const next = { ...(await getSettings()), ...patch };
  await chrome.storage.local.set({ settings: next });
  return next;
}
