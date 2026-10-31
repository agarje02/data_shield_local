// Copy onnxruntime-web and pdf.js files the extension loads into vendor/ (MV3 allows no remote code).
//   npm install && npm run vendor      (from the repo root)
import { copyFileSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const pkgDir = join(here, "..", "..", "node_modules", "onnxruntime-web");
const { version } = JSON.parse(readFileSync(join(pkgDir, "package.json"), "utf-8"));
const out = join(here, "..", "vendor", "ort");
mkdirSync(out, { recursive: true });
// the WASM-only build: SIMD + threads (threads need the cross-origin isolation set in manifest.json)
for (const f of ["ort.wasm.min.mjs", "ort-wasm-simd-threaded.mjs", "ort-wasm-simd-threaded.wasm"]) {
  copyFileSync(join(pkgDir, "dist", f), join(out, f));
}
console.log(`onnxruntime-web ${version} -> ${out}`);

// pdf.js: the text of PDF uploads in built-in mode
const pdfDir = join(here, "..", "..", "node_modules", "pdfjs-dist");
const pdfOut = join(here, "..", "vendor", "pdfjs");
mkdirSync(pdfOut, { recursive: true });
for (const f of ["pdf.min.mjs", "pdf.worker.min.mjs"]) copyFileSync(join(pdfDir, "build", f), join(pdfOut, f));
const pdfVersion = JSON.parse(readFileSync(join(pdfDir, "package.json"), "utf-8")).version;
console.log(`pdfjs-dist ${pdfVersion} -> ${pdfOut}`);
