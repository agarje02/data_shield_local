// Built-in model files: downloaded once from Hugging Face at the commit pinned in model.json,
// checked against the SHA-256 there, then kept in the extension's Cache Storage (unlimitedStorage).
// Used by the settings page (download, remove) and the offscreen page (load).

const MANIFEST_URL = new URL("./model.json", import.meta.url);
const CACHE = "datashield-model";

export async function manifest() {
  return (await fetch(MANIFEST_URL)).json();
}

const fileUrl = (m, name) => `https://huggingface.co/${m.repo}/resolve/${m.revision}/${m.folder}/${name}`;
// cache key: the pinned file, so a new model.json never picks up an older download
const cacheKey = (m, name) => `https://datashield.local/${m.revision}/${name}`;

async function sha256(buf) {
  const d = await crypto.subtle.digest("SHA-256", buf);
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** {installed, files: {name: present}} for the pinned model. */
export async function status() {
  const m = await manifest();
  const cache = await caches.open(CACHE);
  const files = {};
  for (const name of Object.keys(m.files)) files[name] = Boolean(await cache.match(cacheKey(m, name)));
  return { installed: Object.values(files).every(Boolean), files, name: m.name, revision: m.revision,
           bytes: Object.values(m.files).reduce((a, f) => a + f.size, 0) };
}

/**
 * Download every missing file. onProgress({done, total, file}) in bytes over all files.
 * Throws on a network error or a checksum mismatch (nothing unverified is stored).
 */
export async function download(onProgress = () => {}, signal) {
  const m = await manifest();
  const cache = await caches.open(CACHE);
  const total = Object.values(m.files).reduce((a, f) => a + f.size, 0);
  let done = 0;
  for (const [name, info] of Object.entries(m.files)) {
    if (await cache.match(cacheKey(m, name))) {
      done += info.size;
      onProgress({ done, total, file: name });
      continue;
    }
    const res = await fetch(fileUrl(m, name), { signal, cache: "no-store", credentials: "omit" });
    if (!res.ok) throw new Error(`download failed: ${name} (HTTP ${res.status})`);
    const reader = res.body.getReader();
    const buf = new Uint8Array(info.size);
    let at = 0;
    for (;;) {
      const { done: end, value } = await reader.read();
      if (end) break;
      if (at + value.length > info.size) throw new Error(`download failed: ${name} is larger than expected`);
      buf.set(value, at);
      at += value.length;
      done += value.length;
      onProgress({ done, total, file: name });
    }
    if (at !== info.size || (await sha256(buf)) !== info.sha256) {
      throw new Error(`checksum mismatch: ${name}; the download was not kept`);
    }
    await cache.put(cacheKey(m, name), new Response(buf, { headers: { "Content-Type": "application/octet-stream" } }));
  }
  await prune(m);
  return status();
}

/** Remove files of other model versions. */
async function prune(m) {
  const cache = await caches.open(CACHE);
  for (const req of await cache.keys()) {
    if (!req.url.startsWith(`https://datashield.local/${m.revision}/`)) await cache.delete(req);
  }
}

export async function remove() {
  await caches.delete(CACHE);
}

/** Files for Detector.create: {model: Uint8Array, tokenizer, config, thresholds}. */
export async function load() {
  const m = await manifest();
  const cache = await caches.open(CACHE);
  const get = async (name) => {
    const res = await cache.match(cacheKey(m, name));
    if (!res) throw Object.assign(new Error("model not downloaded"), { code: "model_missing" });
    return res;
  };
  return {
    model: new Uint8Array(await (await get("model.onnx")).arrayBuffer()),
    tokenizer: await (await get("tokenizer.json")).json(),
    config: await (await get("config.json")).json(),
    thresholds: await (await get("thresholds.json")).json(),
  };
}
