# Browser extension (Chrome, Edge)

Checks what you paste, drop or upload into AI tools and webmail for sensitive data, before the
site sees it. Detection runs in the local engine on this device; nothing is sent anywhere.

Protected sites: chatgpt.com, chat.openai.com, claude.ai, gemini.google.com, copilot.microsoft.com,
mail.google.com, plus the local test page http://127.0.0.1:8765/ext-test.

## Setup (developer)
1. Start the engine: `python -m engine` (from the `sensitive_data_detector` folder).
2. Register the native host once: `python -m engine install-native-host`
   (per user; undo with `uninstall-native-host`).
3. `chrome://extensions` (or `edge://extensions`) → Developer mode → Load unpacked → this folder.
   The ID must be `kemadmlodfnjgebighmaflcgieihckka` (fixed by `"key"` in manifest.json).

## What it does
| Event | Behaviour |
|---|---|
| Paste / text drop | held, scanned, then inserted as is, inserted masked (`[CREDIT_CARD]`), or dropped, by policy |
| File pick / file drop | held, scanned (PDF, Office, zip, ...), then passed to the site unchanged, or cleared |
| Sending (Enter, Ctrl/Cmd+Enter, Send button) | the message is checked first; if sensitive, a heads-up: **Send anyway**, **Send masked**, **Edit message** (critical → Edit is the default). Clean messages send at once. In Gmail plain Enter is a new line, so only Ctrl+Enter and the Send button are checked |
| Typing | after a 1.5 s pause, a warning bar if the message contains sensitive data |
| Engine not running | everything allowed, red `!` badge on the extension icon (fail-open) |

Policy actions (`configs/policy.yaml`): **block** → only "paste masked" or cancel; **mask** → masked
is the default; **warn** → paste anyway is the default. Files cannot be masked: block/mask mean
"don't upload" by default. "Not sensitive? Report" stores the case in the local feedback database.

Popup: engine status, protect this site on/off, last events (labels and decisions only, no values).

## Files
- `background.js`: the only part that talks to the engine (native messaging, one persistent port)
- `content.js`: event interception on the protected sites; `ui.js`: dialog and warning bar (closed shadow DOM)
- `decide.js`: pure decision logic (`node --test extension/decide.test.js`)

## Updating
After an update or reload the extension adds itself to already-open protected tabs; an older copy left
in a tab steps aside. Copies from before v0.2 cannot do that: reload such tabs once.

## Known limits (MVP)
- Send buttons are found by their label ("Send…", "Submit…", data-testid send-button); a site that
  renames its button needs a pattern update (Enter still works).
- Files over 25 MB are not checked (allowed).
- Verified on the local test page; check each real site after it changes its editor.
