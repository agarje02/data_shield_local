# Product plan: from model to working DLP product

Goal: turn the current detector (v2.3, 20 labels, severity policy) into a working local product:
**one engine on the device, used by a file scanner, a browser extension and an endpoint agent.**
No new model training in this plan. The DevOps label pack and more fine-tuning come after,
with real data.

Everything stays on the device: no cloud calls, raw values never written to logs.

---

## Architecture

```
                        ┌──────────────────────────────────────────────┐
                        │ Engine service (one per device, always on)   │
 Browser extension ──►  │  • model (ONNX, loaded once, ~1.3 GB RAM)    │
 (native-messaging      │  • rules: checksums + secret patterns        │
  host relays)          │  • policy: severity, action, combos          │
                        │  • file extractors (PDF, DOCX, XLSX, ...)     │
 Endpoint agent ─────►  │  • audit log (labels + hashes, no values)    │
 (clipboard, folders,   │  • feedback store (local only)               │
  tray)                 │  API on 127.0.0.1, token-protected           │
 CLI / laptop app ───►  └──────────────────────────────────────────────┘
```

Why one engine: the model is 736 MB and 1.3 GB in RAM. Loading it once and sharing it keeps
the extension and agent light; every client gets the same detections and policy.

Why native messaging for the browser: Chrome starts a small "host" program the extension talks to
over stdin/stdout. Web pages cannot reach it, unlike a plain `http://127.0.0.1` port. The host
relays to the engine with the device token.

---

## Milestones

Each milestone ends with something you can run and a check that proves it works.

### M1. Engine service (base for everything else) — DONE 2026-10-30
Built: `engine/` (core, api, security, audit, paths), `python -m engine` (the review app is served by
it; `python -m app.server` still works). Measured with v2.3 on this laptop: 300 scans, median 96 ms,
p95 1.7 s (long docs), cached repeat 29 ms, RSS flat at 1.30-1.31 GB; no token → 401, foreign
Origin → 403, foreign Host → 403; audit holds host + salted hashes only. 50 tests pass.
Note for clients: `engine.json` can be stale after a hard kill; always confirm with `/v1/health`.

- Refactor `app/server.py` into `engine/` with a versioned API:
  - `POST /v1/scan` `{text, source, context}` → spans, assessment (risk, action, combos), masked text, ms
    - `GET /v1/health`, `GET /v1/policy`, `POST /v1/feedback`
- Security: binds 127.0.0.1 only; a random per-install token (file readable by the user only) is
  required on every request; requests with a browser `Origin` header are rejected.
- Audit log (JSONL, rotating): time, source, label, severity, action, `sha256(salt + value)`;
  never the value.
- Speed: cache by text hash; long texts in chunks; the laptop app keeps working as a client.
- Negative-context list in `policy.yaml` (customer-editable), e.g. `CARD_EXPIRY` not after
  "best before / mfg / use by", `OTP_PIN` not after "ext / extension / room". Tuned on real
  feedback, not on our test set.
- **Check:** API tests; same results as today's app; 1-hour soak with no memory growth.

### M2. File scanning — DONE 2026-10-30
Built: `engine/extract.py` (text/code/logs/CSV by line, PDF page+line, DOCX paragraph, XLSX sheet!cell
with header rows kept as `a | b` context, .eml headers/body/attachments, ZIP recursive; limits for
size, depth, member count, compression ratio), `Engine.scan_document` (one assessment per file, so
combos work across cells), `/v1/scan-file`, `/v1/scan-upload`, review app "Open file" → File scan
card, CLI `python -m engine scan PATH --html --json` (uses the running engine if any; masked
previews unless `--show-values`; exit code 2 if anything critical). Demo folder of 8 files in 1.0 s
with correct locations. 59 tests pass. OCR not done (images reported as skipped).
- `engine/extract/`: one extractor per type, each returns text segments with a location:
  - text, code, config, logs, CSV, JSON, YAML, `.env` → line numbers
  - PDF (text layer) → page + line
  - DOCX → paragraph; XLSX → sheet!cell
  - `.eml` → headers, body, attachments (recursive)
  - ZIP → recursive with limits (depth, file count, total size) against zip bombs
  - images (OCR, Tesseract) → later, optional
- Skip binaries; size caps; per-file timeout.
- CLI: `datashield-local scan <path> --recursive --json|--html`; the laptop app gets "Scan file" with results
  per location.
- **Check:** fixture files for every type with known values; findings point to the right line,
  page or cell.

### M3. Browser extension MVP (Chrome and Edge, Manifest V3) — DONE 2026-10-30
Built: `extension/` (plain JS, no build step; see extension/README.md), `engine/native_host.py`
(stdlib only; 259 ms start, ~40-80 ms per check), `python -m engine install-native-host`
(HKCU registry / macOS + Linux manifest dirs), test page `/ext-test`. Verified in Chrome on the test
page: card+CVV paste blocked → only masked text reached the page; clean paste and clean file pass
untouched; KYC .docx upload blocked (identity kit) and the input cleared; typed password → warning
bar; all events audited without values. 64 Python + 4 JS tests. v0.2 (same day): send check on Enter / Ctrl+Enter / Send
button with a heads-up (send anyway / send masked / edit); protects already-open tabs on install;
stale copies step aside. Verified on the test page and on Gemini (paste blocked; Enter → heads-up,
nothing sent). Still to try: ChatGPT, Claude, Copilot, Gmail.
- Sites first: ChatGPT, Claude, Gemini, Copilot, Gmail. A per-site on/off list.
- Watches paste and submit, plus file uploads (the file is sent to the engine via the native host).
  It does not scan every keystroke.
- On a finding it applies the policy:
  - **block** → stop the submit and show what was found;
  - **mask** → offer "send masked";
  - **warn** → confirm.
- Popup: engine status, recent events, a "this was wrong" button that writes to the feedback store
  (more real data).
- Engine not running: default is **fail-open with a red badge** (allow + warn); enterprise
  setting is fail-closed.
- **Check:** Playwright tests with the unpacked extension on a local test page; manual run on each
  target site.

### Publish kit (prepared locally 2026-10-30; nothing uploaded)
Built: runtime moved into the `datashield_local/` package (root `common.py`, `policy.py`, ... are aliases so
training scripts are unchanged), `pyproject.toml` (wheel = `datashield_local/` only, `datashield-local` command),
`datashield-local download-model` (Hugging Face, SHA256SUMS-verified), `publish/prepare_model.py` →
`publish/hf_model/` (745 MB, verified), model card, Hugging Face Space demo (`publish/hf_space/`),
new README (training docs → docs/TRAINING.md). Wheel installed in isolation works end to end.
Blocked on: a product name (PyPI `datashield-local` and `datashield-local` are taken), HF / GitHub
accounts, LICENSE file, and the user's go-ahead to upload.

### M4. Endpoint agent MVP
- Tray icon (status, pause, open app).
- Clipboard guard: scan on change; notify on critical; optional auto-mask.
- Folder watcher (Downloads, Desktop, Documents by default; configurable): scan new and changed
  files with M2.
- On-demand full scan with an HTML report.
- **Check:** copying a test card + CVV triggers a critical notification; dropping a test file
  into Downloads produces a finding with its location.

### M5. Packaging (Windows and macOS)
- One binary per OS (engine + agent + native host) with PyInstaller.
- Model downloaded on first run, verified by SHA-256, stored in the app-data folder.
- Start at login: Startup entry or service on Windows; `launchd` agent on macOS.
- macOS: Full Disk Access prompt for folder scanning; code signing and notarization need an Apple
  developer account (later).
- **Check:** clean install on a fresh Windows machine and a MacBook; extension connects; scans work.

### M6. Field test and feedback loop
- Use it daily for 2–4 weeks; every "this was wrong" and every review goes into the golden set.
- Measure on real data: false alarms per day, missed items, latency per scan.
- Decide the next fine-tune (and the DevOps pack) from these numbers.

---

## Performance targets

| Path | Target | Today |
|---|---|---|
| Paste / submit (short text) | < 300 ms | ~60–280 ms per short text on this laptop's CPU |
| Document (~4k chars) | < 1 s | ~0.4–0.6 s |
| Engine memory | < 1.6 GB peak | 1.3 GB loaded, ~1.6 GB peak (fp32) |
| MacBook (Apple Silicon) | same or faster | expected; verify in M5 |

If the browser path is too slow: a regex pre-filter for the obvious cases, then the distilled
small model (ROADMAP Phase 4), not int8 (lower accuracy, measured).

---

## Decisions (my defaults; say if you want them different)

| Decision | Default |
|---|---|
| Browser support first | Chrome + Edge (same code); Firefox later |
| Sites first | ChatGPT, Claude, Gemini, Copilot, Gmail |
| Engine down | fail-open with a warning badge |
| Primary OS | Windows (this laptop) and macOS together; the engine is Python, so both work |
| Language for extension | TypeScript, no framework |
| Agent UI | tray icon + the existing web app for details |

## Not in this plan
- New training (v3 / DevOps pack): after M6, with real data.
- Central admin console or cloud reporting: later, for the enterprise tier.
- OCR: optional at the end of M2.
