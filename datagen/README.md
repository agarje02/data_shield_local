# datagen: synthetic data generator

Separate from training: it only writes a frozen dataset folder that `train_ner.py` reads.

```bash
python -m datagen.build --config datagen/configs/v1.yaml --out data/v1
python data/validate_dataset.py data/v1
python data/leakage_check.py data/v1 --extra data/v1/clean_test.jsonl
python -m pytest -q datagen/tests

# v2: 20 labels (adds UPI_ID, CVV, CARD_EXPIRY, OTP_PIN, USERNAME, AUTH_TOKEN, PRIVATE_KEY, CONNECTION_STRING)
python -m datagen.build --config datagen/configs/v2.yaml --labels configs/labels_v2.yaml --out data/v2
python data/validate_dataset.py data/v2 --labels configs/labels_v2.yaml
```

Label policies (what counts as each new type, and which look-alikes don't) are in the
`components.py` docstring. `COMBOS` there are sentences with several values (card + expiry + CVV).

| File | What it holds |
|---|---|
| `values.py` | `SensitiveValues` (one method per label, checksum-valid Aadhaar/cards), `LookalikeValues` (harmless twins: AWB, UPI ref, SKU, GSTIN, git SHA, version strings, ports, masked cards, placeholder keys...), `Filler` |
| `components.py` | Pure data: PII cues, look-alike cues (often the *same wording* as PII cues), sentence frames, neutral sentences, record field names |
| `build.py` | Composes documents (email, chat, log, ticket, memo, config, form, table, mixed), splits components disjointly across train/val/test, writes JSONL + manifest |
| `configs/*.yaml` | One file per dataset version: sizes, clean-document share, look-alike share, style mix |

## Why it is built this way
- **Realistic base rate**: ~45% of documents contain no PII, and PII documents are mostly clean sentences.
- **Contrast**: the same frame and often the same cue appear with a sensitive value and with a harmless one
  (`card number 4111…` vs `card number XXXX-XXXX-XXXX-1234`), so only the value and context decide.
- **Disjoint components**: every cue, frame, neutral sentence and record key belongs to one split, so the
  test score measures unseen wording.
- **clean_test.jsonl**: PII-free documents for the false-positives-per-1,000-documents metric.

To add coverage, add cues/keys to `components.py` and a method to `values.py`, then build a new version
(`configs/v2.yaml`). Never overwrite an existing version.
