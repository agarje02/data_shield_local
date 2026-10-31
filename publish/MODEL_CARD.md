---
license: apache-2.0
base_model: microsoft/deberta-v3-base
pipeline_tag: token-classification
library_name: onnx
language: [en]
tags: [pii, dlp, secrets, ner, privacy, aadhaar, pan, upi, onnx]
---

# DataShield Local v2.3 (ONNX)

A token-classification model that finds sensitive values in text: personal data, Indian IDs,
payment data and credentials. It is the model behind the
[DataShield Local](https://github.com/agarje02/data_shield_local) local engine,
which adds checksum / format rules, a severity policy and file, browser and clipboard scanning.
Everything runs on your own device; this repo only hosts the weights.

## Labels (20)

| Group | Labels |
|---|---|
| Personal | PERSON, EMAIL, PHONE, ADDRESS, DATE_OF_BIRTH, USERNAME, IP_ADDRESS |
| Indian IDs | AADHAAR (Verhoeff-checked), PAN |
| Financial | CREDIT_CARD (Luhn-checked), CARD_EXPIRY, CVV, BANK_ACCOUNT (IBAN-checked), UPI_ID, OTP_PIN |
| Credentials | PASSWORD, API_KEY, AUTH_TOKEN, PRIVATE_KEY, CONNECTION_STRING |

## Use

With the engine (rules, policy, files, browser extension):
```bash
pip install "datashield-local @ git+https://github.com/agarje02/data_shield_local"   # PyPI release soon
datashield-local download-model
datashield-local                                # http://127.0.0.1:8765
datashield-local scan ./folder --html report.html
```

Files: `model.onnx` (fp32, opset 17, inputs `input_ids` + `attention_mask`, output `logits`),
`tokenizer.json`, `config.json` (`id2label`, BIO tags), `thresholds.json` (per-label minimum
scores tuned on validation), `labels.yaml`, `SHA256SUMS`.

### Browser copy (`web/`)
`web/model.onnx` is the same model for the browser extension, 211 MB: 4-bit weight-only linear
layers (MatMulNBits, blocks of 32) and an int8 embedding table. It runs in onnxruntime-web (WASM)
and scores the same as fp32 on 400 v2.3 test documents (F1 0.974 both; clean false alarms 27 vs
43 per 1,000). With the extension's JavaScript decoding it returns exactly the spans of the Python
engine on 322 test texts. The extension pins the files by SHA-256 (`web/SHA256SUMS`).

## Results

Synthetic, template-disjoint test sets (wording held out from training), model + checksum rules:

| Test | Strict span F1 |
|---|---|
| v2.3 test set, 20 labels (3,033 spans) | 0.978 [0.97, 0.98] |
| v1 test set, 12 shared labels (1,870 spans) | 0.992 |
| 8 real reviewed documents, 12 labels (1,322 spans) | 0.956 (partial-match 0.978) |

Per label on the v2.3 test: 1.00 for PERSON, EMAIL, PHONE, ADDRESS, CREDIT_CARD, IP_ADDRESS,
UPI_ID, PRIVATE_KEY; AADHAAR 0.985, PAN 0.995, PASSWORD 0.990, USERNAME 0.994, CVV 0.951,
API_KEY 0.960, AUTH_TOKEN 0.960, BANK_ACCOUNT 0.943, CONNECTION_STRING 0.908, OTP_PIN 0.898,
CARD_EXPIRY 0.889.

False alarms: 36 per 1,000 clean documents on the v2.3 clean set, almost all from two harmless
phrasings held out of training ("best before 09/27", "extension 6152"); the engine's policy
drops these contexts.

## Training

Fine-tuned from `microsoft/deberta-v3-base` (MIT) on 20,000 synthetic documents (emails, chats,
logs, configs, CSV/JSON records, forms) with exact character spans; look-alike negatives (order
numbers, AWB, GSTIN, IFSC, git SHAs, UUIDs, public keys, placeholders) teach what not to flag.
3 epochs, lr 3e-5, batch 16, max 512 tokens with stride 128, bf16 on one L4.

## Limitations

- Trained on synthetic data; real-world accuracy is measured on only 8 documents so far.
- English text and Indian / US formats; other scripts and countries' IDs are not covered.
- Context labels (PERSON, ADDRESS, PASSWORD) can miss unusual wording; short numbers (CVV, OTP)
  depend on nearby words.
- Use it to reduce leaks, not as a guarantee. Review flagged and unflagged data that matters.
