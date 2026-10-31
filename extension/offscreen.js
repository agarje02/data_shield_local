// Offscreen page: holds the built-in model while it is used. The background worker opens it on the
// first check and closes it after it reports being idle, giving the memory (~0.5 GB) back.
import { Detector } from "./engine/detector.js";
import * as store from "./engine/modelstore.js";
import { Policy } from "./engine/policy.js";
import { detectFile, detectText } from "./engine/service.js";
import * as ort from "./vendor/ort/ort.wasm.min.mjs";

const IDLE_MS = 5 * 60 * 1000;
ort.env.wasm.wasmPaths = chrome.runtime.getURL("vendor/ort/");

let ready = null;          // Promise<{detector, policy}>
let idleTimer = null;
let busy = 0;

function threads() {
  // threads need cross-origin isolation (manifest COOP/COEP); leave cores for the page the user is on
  if (!self.crossOriginIsolated) return 1;
  return Math.max(1, Math.min(4, Math.floor((navigator.hardwareConcurrency || 2) / 2)));
}

function load() {
  ready ??= (async () => {
    const t0 = performance.now();
    const [files, policyCfg] = await Promise.all([store.load(), fetch("engine/policy.json").then((r) => r.json())]);
    const detector = await Detector.create(ort, files, { threads: threads() });
    console.info(`DataShield model loaded in ${Math.round(performance.now() - t0)} ms, ${threads()} thread(s)`);
    return { detector, policy: new Policy(policyCfg) };
  })().catch((e) => {
    ready = null;              // let the next request try again (e.g. after the download finishes)
    throw e;
  });
  return ready;
}

let pdfjs = null;
async function pdfText(bytes) {
  if (!pdfjs) {
    pdfjs = await import("./vendor/pdfjs/pdf.min.mjs");
    pdfjs.GlobalWorkerOptions.workerSrc = chrome.runtime.getURL("vendor/pdfjs/pdf.worker.min.mjs");
  }
  const doc = await pdfjs.getDocument({ data: bytes, isEvalSupported: false, disableFontFace: true }).promise;
  const pages = [];
  for (let i = 1; i <= doc.numPages; i++) {
    const content = await (await doc.getPage(i)).getTextContent();
    pages.push(content.items.map((it) => it.str + (it.hasEOL ? "\n" : "")).join(""));
  }
  await doc.destroy();
  return pages;
}

function touch() {
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => {
    if (busy === 0) chrome.runtime.sendMessage({ type: "offscreen-idle" }).catch(() => {});
  }, IDLE_MS);
}

const b64ToBytes = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));

async function handle(msg) {
  const { detector, policy } = await load();
  const t0 = performance.now();
  if (msg.op === "warmup") return { ok: true, result: { ms: 0 } };
  if (msg.op === "text") {
    const r = await detectText(detector, policy, msg.text);
    return { ok: true, result: { ...r, ms: performance.now() - t0 } };
  }
  if (msg.op === "file") {
    const bytes = b64ToBytes(msg.data_b64);
    const r = await detectFile(detector, policy, msg.name, bytes, { pdfText });
    return { ok: true, result: { ...r, size: bytes.length, ms: performance.now() - t0 } };
  }
  return { ok: false, error: "unknown_op" };
}

// one check at a time: a model session runs one inference at a time
let queue = Promise.resolve();

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg?.target !== "offscreen") return false;
  busy++;
  touch();
  queue = queue.then(() => handle(msg))
    .then(reply, (e) => reply({ ok: false, error: e.code || "detector_error", message: String(e.message || e) }))
    .finally(() => { busy--; touch(); });
  return true;
});
touch();
