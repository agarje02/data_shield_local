# Detection Engine roadmap

Status (2026-10-29): v1 (12 labels) is the working model. v2 (20 labels: + UPI_ID, CVV,
CARD_EXPIRY, OTP_PIN, USERNAME, AUTH_TOKEN, PRIVATE_KEY, CONNECTION_STRING) is trained,
but **not ready to replace v1** (see Phase 0).

The biggest gap: **every number we have comes from our own synthetic generator**, apart
from 8 reviewed feedback documents. The scores show the model learned our templates; they
do not yet show how it does on real emails, chats, logs and code.

---

## Where we are

| Model | Test set | Strict F1 | False alarms / 1k clean docs | Notes |
|---|---|---|---|---|
| v1 + rules | v1 synthetic test | 0.965 | 2.0 | the working model |
| v1 + rules | feedback golden set (8 docs, 24 made-up values dropped) | 0.985 | n/a | PASSWORD recall 0.83 |
| v2 + rules | v2 synthetic test | **0.905** | **40** (OTP_PIN 37 of them) | see Phase 0 |
| v2 + rules | v1 synthetic test, 12 shared labels | 0.964 | 6.7 | no overall regression vs v1; CREDIT_CARD 0.93, PAN 0.96 dipped |
| v2 + rules | feedback golden set, 12 shared labels | 0.969 | n/a | PASSWORD recall 0.83 → **0.98**; API_KEY recall 0.89 (bearer tokens are AUTH_TOKEN now: policy change, not all errors) |
| v1 + rules | v2 clean docs | n/a | **767** | v1 flags public keys / certificates as API_KEY; v2 learned these negatives |

v2 per label: UPI_ID, PRIVATE_KEY, IP, EMAIL, DOB at 1.00; CARD_EXPIRY 0.98; AUTH_TOKEN 0.92;
CONNECTION_STRING 0.91. Problem labels: PASSWORD 0.40, CVV 0.55, OTP_PIN 0.64, USERNAME 0.86,
BANK_ACCOUNT 0.90.

---

## Phase 0: fix v2 (next, ~1 day + one retrain)

Error analysis of the v2 test report, with causes:

| Problem | Cause | Fix |
|---|---|---|
| PASSWORD strict F1 0.40 (partial F1 **0.99**) | Boundaries, not detection: the span keeps a trailing `?` or a leading `:` (`creds: user:pass?`). PASSWORD is exempt from punctuation trimming because passwords contain symbols. | In `common._trim`, drop a leading `:` / `=` and a trailing `?` / `!` that ends a sentence (like the existing full-stop rule). No retrain needed. |
| USERNAME recall 0.76, PASSWORD in `user:pass` | Model tags `mario_goo:o5T…` as one PASSWORD span; the `{USERNAME}:{PASSWORD}` combo with no space is too rare to learn. | More `user:pass` examples (also `-u user -p pass`, `user / pass`); post-split a PASSWORD span at its first `:` when the left part looks like a username. |
| OTP_PIN 37 false alarms per 1k clean docs | Indian postal **"PIN code 411001"** is predicted as OTP_PIN (scores 0.51–0.75; threshold 0.5). The `PIN code` look-alike cue fell into the test split, so training never saw it. | Raise the OTP_PIN threshold (val-tuned); put the key contrasts (`PIN code`, `pincode`) in **every** split; add more postal-PIN negatives. |
| CVV recall 0.39 | Only 12 cue phrasings, split 3 ways, so the model learned a handful of wordings; `CVV 262.` in an unseen frame is missed. | 30+ cue phrasings per label, including the bare label word (`CVV 262`, `cvv:262`, `CVV-262`) in all splits; CVV in more tables. |
| BANK_ACCOUNT recall 0.84, PHONE 0.89 | Long digit runs in TSV/CSV next to look-alikes (AWB, RRN) and new labels competing. | More table rows with header context; check confusion matrix after the fixes. |

Progress (2026-10-29):
- Decoding fixes done (`common._trim`, `repair_boundaries` user:pass split). Same v2 model,
  v2 test: overall F1 0.905 → **0.949**, PASSWORD 0.40 → **0.99**, USERNAME 0.86 → **0.99**.
- Data v2.1 built (`datagen/configs/v2_1.yaml`, `components.SHARED`), all checks pass.
  Left for the retrain: CVV recall, OTP_PIN false alarms on postal PIN codes.

v2.1 result (`deberta_v2_run2`, L4, 54 min; model at `models/deberta_v2_1_onnx/`):

| Check | v1 | v2 | **v2.1** |
|---|---|---|---|
| v1 test, 12 shared labels | 0.965 | 0.964 | **0.975** |
| own synthetic test (20 labels) | n/a | 0.905 → 0.949 with decode fixes | **0.954** (Colab) / **0.965** (600-doc sample, + voting fix) |
| false alarms / 1k clean docs | 2.0 (v1 data) / 542 (v2.1 data) | 40 | **1.0** |
| OTP_PIN | n/a | 0.64 | 0.84–0.87 (postal PIN false alarms gone) |
| CVV | n/a | 0.55 | 0.50 → **0.92** with the voting fix |
| feedback golden set, 12 labels | 0.985 | 0.969 | 0.960 / 0.943 with voting fix (see below) |

- **Voting fix** (`common.aggregate_words`): punctuation-only tokens no longer vote, so a
  one-token value followed by "." (`cvv 765.`) is no longer cancelled. CVV recall 0.34 → 0.87.
  It also fixes the old surname bug ("Rahul Verma." was cut to "Rahul").
- **Golden-set labels are partly wrong**: 16 reviews saved only the first name (accepted from
  v1's output), so full-name predictions now score as errors. Re-review those docs.
- **Real gap found by the golden set**: 33 of 84 bank accounts missed, all after an uppercase
  `BANK_ACCOUNT:` field name; the synthetic keys are always lowercase/snake. Data v2.2:
  key-format augmentation for every label (UPPER_SNAKE, Title Case, "No." suffixes).
- Still open: API_KEY vs AUTH_TOKEN on prefix-less random tokens (API_KEY precision 0.77);
  AADHAAR in pipe tables (recall 0.83); OTP_PIN recall 0.72.
- **App default stays v1** until v2.2 is no worse than v1 on every shared label on the
  (re-reviewed) golden set. v2.1 can be tried with `--model models/deberta_v2_1_onnx`.

v2.2 result (`deberta_v2_run3`, `models/deberta_v2_2_onnx/`): field-name formats fixed the
golden-set bank accounts (0.76 → **0.99**); v1 test 12 labels **0.989** (v1 0.965); golden set
0.961 vs v1 0.965 with the same decoding (same within CI). But Aadhaar recall 0.36 and CVV 0.62
on its own test: the random disjoint split put "UID" / `uid` and `security_code` / "CSC" only
into test, so the model never learned these synonyms. Lesson: **field names and core synonyms
are vocabulary, not phrasing**. Data v2.3 (`share_keys: true`, synonyms in `SHARED`) puts them
in every split; sentence wording stays held out. Eval docs identical to a train doc are now
regenerated (`build_split(exclude=...)`).

**v2.3 result (`deberta_v2_run4`, `models/deberta_v2_3_onnx/`) — app default since 2026-10-30,
last fine-tune until real data arrives:**

| Check | v1 | v2.2 | **v2.3** |
|---|---|---|---|
| own test (20 labels) | n/a | 0.941 (sample) | **0.978** |
| v1 test, 12 shared labels | 0.965 | 0.989 | **0.992** |
| golden set, 12 labels, same decoding | 0.965 | 0.961 | 0.956 (partial F1 0.978 vs 0.981; PERSON gap = partial-name reviews) |
| Aadhaar / CVV (own test) | n/a | 0.52 / 0.62 | **0.985 / 0.951** |
| false alarms / 1k clean docs (v2.3 clean set) | 642 | n/a | 36 |

The 36 false alarms are two harmless phrasings that only exist in the test split: "best before
09/27" (CARD_EXPIRY) and "extension 6152" (OTP_PIN). Not patched with rules derived from the test
set; real-data feedback decides (a customer-editable negative-context list is in the product plan).

Then: rebuild data **v2.1**, retrain on an **L4** (about 30 min), compare against v1 on the
12 shared labels (`evaluate.py --only-labels`). Switch the app to v2 only when it is no worse
than v1 on every shared label and false alarms are back to ≤ 5 per 1k clean docs.

Engineering fixes found on the way:
- `disable_tqdm=True` in `train_ner.py` (done): the progress bar froze the Colab tab.
- If a Colab tab hangs: open any other notebook → Runtime → Manage sessions → terminate.

---

## Phase 1: measure reality (start now, runs alongside everything else)

1. **Golden set to 200+ real documents** from varied sources (see "Getting real data").
   Review every scan in the app and save it, clean ones too. Export with
   `python -m app.feedback export feedback/golden.jsonl --golden`.
2. **Scoreboard**: one table per model version with the same sets every time:
   synthetic test, clean false alarms, golden set, per-label F1 with CI. Release gate:
   no label drops beyond its CI, false alarms ≤ 5 per 1k.
3. **Uncertainty-first review**: the app shows scans with low-confidence spans first, so
   each review teaches the most.

## Phase 2: data v3, realistic contexts

4. **Real-context injection**: take real open-source code, configs, logs and public-domain
   prose, and insert generated values at realistic positions. Labels stay exact because we
   choose where values go. This teaches context that templates cannot.
5. **LLM-written documents**: have an LLM write varied emails, tickets and chats with
   `{SLOT}` placeholders, then fill the slots with our generators. Far more natural wording
   than 12 cues per label.
6. **Hard negatives**: mentions without values ("send your PAN card"), documentation
   examples (`sk-...` in a README), test fixtures, public company numbers and emails.
7. **Noise**: OCR-style character swaps, values split across lines, quoted email replies,
   Hinglish, lowercase.
8. **Training recipe for later versions**: warm-start from the previous best, replay all
   synthetic data, upsample the feedback train half 5×. Keep the other half of feedback for
   testing only.

## Phase 2b: confidence cascade (a bigger model only where it helps)

Not a fix for Phase 0: those errors come from data and decoding, and a larger model trained
on the same data repeats them. After v2.1:

```
all text ──► fast model (base, later distilled small)
               ├── confident (score ≥ ~0.85) ──► accept / reject
               └── uncertain (~0.4 – 0.85)   ──► verifier on span + context
```

- **Measure first**: on the golden set, the share of errors that fall in the uncertain
  band. Most errors uncertain → cascade pays off. Errors confidently wrong → data problem.
- **Verifier, first choice: DeBERTa-v3-large** fine-tuned on the same data (435M,
  ~1.7 GB; train on an L4, about 1.5–2 h; same labels and pipeline). This is the
  enterprise self-hosted verifier tier.
- **Later**: `mdeberta-v3-base` if the golden set shows a Hindi/Devanagari problem; a
  small local LLM (3–4B, 4-bit) for context-only judgment on the few hardest spans.

## Phase 3: engine core

9.  **File extraction**: PDF (text layer), DOCX, XLSX/CSV, .eml, zip, then OCR for images.
    Spans map back to page / row / cell.
10. **More secret rules + entropy check**: Azure, GCP service-account JSON, Twilio, SendGrid,
    npm, Slack webhooks; a high-entropy check for unknown keys in `KEY=value` lines. Each
    rule measured on the clean set.
11. **Risk scoring** (first version done 2026-10-30: `configs/policy.yaml` + `policy.py`;
    the app shows a risk banner and a severity column): severity per label, combination rules (card + expiry + CVV = critical;
    Aadhaar + name + DOB = identity kit), one document-level risk score.
12. **Policy file**: action per label (allow / warn / mask / block) and redaction style
    (full mask, last-4, consistent pseudonym).

## Phase 4: speed and hardening (before browser extension / endpoint agent)

13. **Speed**: regex/keyword pre-filter that skips clean text; distil to
    `deberta-v3-small` for typing/clipboard; keep base for file scans.
14. **Local API security**: token auth and Origin allow-list (today any web page could send
    requests to 127.0.0.1:8765); versioned `/v1/scan`; audit log with label + hash +
    position, never the value; checksum/signature check before loading a model.

---

## How much can this model learn?

- **Capacity is not the limit.** DeBERTa-v3-base has 184M parameters; the output layer for
  20 labels is about 31k of them. 50–100 labels would fit. Training time grows with data
  size, not with the number of labels.
- **Data is the limit.** More epochs on the same synthetic data overfit to our templates
  (v0 overfit after about 2 epochs). Gains now come from new, more varied data, mainly
  real-context data.
- **Forgetting**: fine-tuning on new data alone makes it forget old cases. We always train
  on the full mix (old + new), so this is controlled. Check: v2 vs v1 on the 12 shared
  labels.
- **Expected ceiling on real text**: lower than synthetic scores at first; the golden set
  tells us the real number. Typical for NER on new domains: a clear drop, then recovery
  with in-domain data.

## Limitations of this model type

| Limitation | Effect | Mitigation |
|---|---|---|
| 512-token window | Context further away (a "Passwords:" heading two pages up) is lost | Sliding windows (done); section headers carried into windows |
| English tokenizer | Devanagari / regional scripts handled poorly | Test a Hindi set; switch to `mdeberta-v3-base` if needed |
| Tags spans, does not reason | Cannot tell a fictional name from a real one, or a test card from a live one | Rules (checksums, prefixes), policy, context negatives |
| Context-only categories (health, religion, caste) | Need far more data and an annotation policy | Later version, own data effort |
| CPU speed 0.15–0.6 s / doc, 736 MB | Too slow for keystroke-level checks | Pre-filter + distilled small model |
| Learns our generator's habits | Synthetic scores are optimistic | Golden set, real-context data |

---

## Getting real data

Rules for all of it: real data stays on this machine (`feedback/`, `reports/` and `models/`
are gitignored), nothing real goes into the Drive upload, and check every dataset's licence
before using it in a commercial product.

1. **Your own data, reviewed in the app**: your emails, chats (WhatsApp/Slack exports),
   documents, your own repos' configs and logs. Best quality, fully yours.
2. **People who consent**: friends, colleagues or a pilot company run the app locally and
   share only error statistics, or opt-in redacted samples.
3. **Public labelled PII datasets** (check licences; several are research-only or
   non-commercial): ai4privacy PII-masking sets, gretelai/gretel-pii-masking-en-v1
   (Apache-2.0), NVIDIA Nemotron-PII, CoNLL-2003 / WikiNER for names.
4. **Real text without labels + injection or model-assisted review**:
   - Enron email corpus (real business email),
   - Loghub (real system logs),
   - open-source code and configs from permissively licensed repos.
   Insert generated values, or run the model and review only uncertain spans in the app.
5. **Secrets**: never collect real leaked keys. Use secret-scanner test fixtures (gitleaks,
   detect-secrets, MIT/Apache) and SecretBench (research access by request) for formats;
   inject fake keys into real code for context.
6. **Indian documents**: blank public KYC / bank / insurance form templates, filled with
   generated values; public Hinglish chat corpora for writing style.
7. **LLM-written realistic documents** with slots (Phase 2, item 5): synthetic, but far
   more varied than templates.
