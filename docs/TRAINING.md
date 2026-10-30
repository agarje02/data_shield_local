# Sensitive Data Detector: Detection Engine v0

Fine-tunes `microsoft/deberta-v3-base` for token classification (BIO NER) to find
sensitive data: PII, Indian IDs, financial data and secrets. Training runs on Google Colab (browser);
data, code and checkpoints live in Google Drive.

Labels (v0, `configs/labels.yaml`): PERSON, EMAIL, PHONE, ADDRESS, DATE_OF_BIRTH, AADHAAR,
PAN, CREDIT_CARD, BANK_ACCOUNT, IP_ADDRESS, API_KEY, PASSWORD.

## The loop

| Step | Where | Command |
|---|---|---|
| 1. Build data | local | `python data/synth_generator.py --out data/v0` |
| 2. Preflight | local | `python data/validate_dataset.py data/v0` and `python data/leakage_check.py data/v0` (both must pass) |
| 3. Upload | local → Drive | zip this folder (skip `.venv/`, `runs/`) as `sdd_upload.zip` and upload it to the root of My Drive (Replace); notebook cell 2 unpacks it into `MyDrive/sdd/` |
| 4. Train | Colab | open `colab_first.ipynb`, T4 GPU, run all cells (re-run = resume) |
| 4b. Thresholds | Colab | notebook cell 4b: `tune_thresholds.py` picks per-label minimum scores on **val** |
| 5. Test | Colab | notebook cell 5 → `MyDrive/sdd/reports/<run>_test.json` |
| 6. Download | Drive → local | `MyDrive/sdd/runs/<RUN_ID>/best/` syncs via Google Drive for Desktop |
| 7. Local test | local | `python evaluate.py --model <best> --data data/v0/test.jsonl` / `python predict.py --model <best> --text "..."` |
| 8. Feedback | local | read `false_negatives` / `false_positives` in the report, then improve the generator and build `data/v1` |

## Local setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # plus torch if not installed
.venv/Scripts/python -m pytest -q tests
```

Smoke test (CPU, a few minutes). This catches bugs before you spend GPU time:
```bash
python train_ner.py --data data/v0 --out runs/smoke --model microsoft/deberta-v3-small \
    --limit 200 --max-steps 10 --eval-steps 5
python evaluate.py --model runs/smoke/best --data data/v0/test.jsonl --limit 50
```

## Files

| File | Purpose |
|---|---|
| `common.py` (now `sdd/common.py`) | span ↔ BIO conversion, sliding-window `Predictor` with word-level label voting, masking. Shared by train, eval and predict |
| `data/synth_generator.py` | template + Faker generator with exact offsets, valid Aadhaar (Verhoeff) and card (Luhn) numbers, hard negatives, template-disjoint splits |
| `data/validate_dataset.py` | schema, offsets, overlaps, label counts |
| `data/leakage_check.py` | exact duplicates, shared values, 8-gram overlap of eval vs. train |
| `train_ner.py` | HF Trainer, 512-token windows with stride 128, fp16 on T4 / bf16 on L4+, best model chosen on **val** span F1, auto-resume |
| `evaluate.py` | strict / partial / label-agnostic span P/R/F1 per label, 95% bootstrap CI, error examples |
| `tune_thresholds.py` | per-label score thresholds tuned on val (recall-first), written to `best/thresholds.json` |
| `predict.py` | spans and masked text for a string or file |

## Output of a run (`runs/<RUN_ID>/best/`)
Model + tokenizer, `labels.yaml`, `thresholds.json` (per-label minimum score, default 0),
and `manifest.json` (model, data manifest with sha256, config, val metrics, device, precision).

## Rules
- The test split is for reporting only. Never choose a model or threshold on it.
- Every new data version gets a new folder (`data/v1`, …). Never overwrite one.
- Scores on synthetic test data are optimistic. Before trusting the model, also test it on
  real-style, hand-labelled text (put it in the same JSONL format and pass it to `evaluate.py --data`).
