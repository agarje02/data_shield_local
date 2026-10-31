"""Reference outputs from the Python detector for the extension's JavaScript port.

  python tests/make_js_fixtures.py [--model models/deberta_v2_3_onnx_q4] [--docs 300]

Writes extension/engine/fixtures/ (git-ignored, regenerate any time):
  tokenizer.json  token ids, offsets and 512/128 windows per text
  decode.json     per-token probabilities going into word voting, and the spans coming out
  rules.json      validator verdicts and rescue results
  policy.json     filter + assess results for the predicted spans
  e2e.json        final spans of Predictor.predict, for the full JS pipeline

All offsets are converted to UTF-16 code units, the unit JavaScript strings use.
"""
import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from datashield_local import validators  # noqa: E402
from datashield_local.common import (Predictor, aggregate_words, read_jsonl, repair_boundaries,  # noqa: E402
                                     tokens_to_spans)
from datashield_local.policy import Policy  # noqa: E402

OUT = ROOT / "extension" / "engine" / "fixtures"

# Texts that stress normalization and offsets: whitespace runs, tabs, CR/LF, leading and trailing
# space, combining marks (NFC), emoji (surrogate pairs), Devanagari, CJK, unknown symbols.
TRICKY = [
    "", " ", "a", "  leading spaces", "trailing spaces   ", "tabs\tand\nnew\r\nlines\n\n\nhere",
    "Café café́ été", "emoji 😀 in 👍🏽 text 🇮🇳 flag", "नमस्ते राहुल, आधार 2345 6789 0125",
    "中文 テキスト 한국어", "weird ⨁ ∰ ⟁ symbols ☃", "nbsp here em　ideo",
    "zero​width﻿bom", "  \n  mixed \t whitespace \n  ", "end with newline\n",
    "card 4111 1111 1111 1111 exp 09/27 cvv 123.", "login rahul.verma92:Mumbai@1990 now",
    "postgres://app:S3cret!2024@db.internal:5432/orders, then", "AKIAIOSFODNN7ABCDEFG and eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "-----BEGIN EC PRIVATE KEY-----\nMHcCAQEEIBkg4LVWM9nuwNSk3yByxZpYRTBnVJk5oSc\n-----END EC PRIVATE KEY-----",
    "best before 09/27, PIN code 411001, OTP 482913, room 312",
]


def u16(text):
    """code point index -> UTF-16 index"""
    out = [0]
    for ch in text:
        out.append(out[-1] + (2 if ord(ch) > 0xFFFF else 1))
    return out


def conv_span(text, sp, idx=None):
    idx = idx or u16(text)
    return {**sp, "start": idx[sp["start"]], "end": idx[sp["end"]]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(ROOT / "models" / "deberta_v2_3_onnx_q4"))
    ap.add_argument("--data", default=str(ROOT / "data" / "v2_3" / "test.jsonl"))
    ap.add_argument("--docs", type=int, default=300)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    docs = [r["text"] for r in read_jsonl(args.data)]
    random.Random(7).shuffle(docs)
    docs = docs[:args.docs]
    long_doc = " ".join(docs[:12])                       # several windows
    texts = TRICKY + docs + [long_doc]

    pred = Predictor(args.model, batch_size=4)
    tok = pred.tokenizer

    # tokenizer + windows
    rows = []
    for t in texts:
        idx = u16(t)
        enc = tok(t, add_special_tokens=True, return_offsets_mapping=True)
        win = tok(t, truncation=True, max_length=512, stride=128, return_overflowing_tokens=True,
                  return_offsets_mapping=True)
        rows.append({"text": t, "ids": enc["input_ids"],
                     "offsets": [[idx[s], idx[e]] for s, e in enc["offset_mapping"]],
                     "windows": win["input_ids"]})
    (OUT / "tokenizer.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    # decode: capture the per-token probabilities Predictor.predict votes on (first 40 docs + long one)
    dec = []
    for t in texts[len(TRICKY):len(TRICKY) + 40] + [long_doc]:
        enc = tok([t], truncation=True, max_length=512, stride=128, return_overflowing_tokens=True,
                  return_offsets_mapping=True, padding=True, return_tensors="np")
        offsets = enc["offset_mapping"].tolist()
        probs = pred._probs(enc["input_ids"], enc["attention_mask"])
        best = {}
        for w in range(len(probs)):
            content = [i for i, sid in enumerate(enc.sequence_ids(w)) if sid is not None]
            first, last = content[0], content[-1]
            for i in content:
                s, e = offsets[w][i]
                if s == e:
                    continue
                c = min(i - first, last - i)
                if (s, e) not in best or c > best[(s, e)][0]:
                    best[(s, e)] = (c, probs[w][i])
        toks = sorted(((s, e, p) for (s, e), (_, p) in best.items()), key=lambda x: (x[0], x[1]))
        words = aggregate_words(t, toks, pred.id2label)
        spans = repair_boundaries(t, tokens_to_spans(t, words, pred.id2label), pred.label_types)
        idx = u16(t)
        dec.append({"text": t,
                    "tokens": [[idx[s], idx[e], [round(x, 6) for x in p]] for s, e, p in toks],
                    "words": [[idx[s], idx[e], lid, round(pr, 6)] for s, e, lid, pr in words],
                    "spans": [conv_span(t, sp, idx) for sp in spans]})
    (OUT / "decode.json").write_text(json.dumps({"id2label": pred.id2label, "docs": dec}, ensure_ascii=False),
                                     encoding="utf-8")

    # end to end + policy
    policy = Policy()
    e2e, pol = [], []
    results = pred.predict(texts)
    for t, spans in zip(texts, results):
        idx = u16(t)
        e2e.append({"text": t, "spans": [conv_span(t, sp, idx) for sp in spans]})
        kept, dropped = policy.filter_spans(t, spans)
        a = policy.assess(kept)
        pol.append({"text": t, "spans": [conv_span(t, sp, idx) for sp in spans],
                    "kept": [conv_span(t, sp, idx) for sp in kept], "dropped": len(dropped),
                    "risk": a["risk"], "score": a["score"], "action": a["action"], "counts": a["counts"],
                    "combos": [c["name"] for c in a["combos"]]})
    (OUT / "e2e.json").write_text(json.dumps({"thresholds": pred.thresholds, "docs": e2e}, ensure_ascii=False),
                                  encoding="utf-8")
    (OUT / "policy.json").write_text(json.dumps(pol, ensure_ascii=False), encoding="utf-8")

    # rules: every gold value of the test set plus look-alikes, and rescue on every text
    gold = [(sp["label"], r["text"][sp["start"]:sp["end"]]) for r in read_jsonl(args.data) for sp in r["spans"]]
    extra = [("AADHAAR", "2345 6789 0124"), ("CREDIT_CARD", "4111 1111 1111 1112"), ("PAN", "ABCDE1234F"),
             ("BANK_ACCOUNT", "GB82 WEST 1234 5698 7654 32"), ("BANK_ACCOUNT", "GB82 WEST 1234 5698 7654 33"),
             ("PHONE", "1800 123 4567"), ("PHONE", "7845123690"), ("PHONE", "+1 (415) 555-0132 ext 22"),
             ("IP_ADDRESS", "127.0.0.1"), ("IP_ADDRESS", "::1"), ("IP_ADDRESS", "300.1.2.3"),
             ("IP_ADDRESS", "2001:db8::8a2e:370:7334"), ("IP_ADDRESS", "[fe80::1]"), ("IP_ADDRESS", "0.0.0.0"),
             ("API_KEY", "AKIAXXXXXXXXXXXXXXXX"), ("UPI_ID", "rahul@gmail.com"), ("CARD_EXPIRY", "13/27"),
             ("CONNECTION_STRING", "postgres://localhost/db"), ("CONNECTION_STRING", "Server=x;User Id=a;Password=${DB_PW};"),
             ("CONNECTION_STRING", "Server=x;User Id=a;Password=Hunter2!;"), ("PRIVATE_KEY", "-----BEGIN PUBLIC KEY-----\nMII\n-----END PUBLIC KEY-----"),
             ("CVV", "१२३"), ("OTP_PIN", "48291"), ("USERNAME", "has space"), ("AUTH_TOKEN", "Bearer xxxxxxxxxxxxxxxxxxxx")]
    seen, verdicts = set(), []
    for label, value in gold + extra:
        if (label, value) not in seen:
            seen.add((label, value))
            verdicts.append([label, value, validators.is_valid(label, value)])
    rescued = []
    for t in texts:
        idx = u16(t)
        rescued.append({"text": t, "spans": [conv_span(t, sp, idx) for sp in validators.rescue(t, [])]})
    (OUT / "rules.json").write_text(json.dumps({"verdicts": verdicts, "rescue": rescued}, ensure_ascii=False),
                                    encoding="utf-8")
    print(f"{OUT}: {len(texts)} texts, {len(dec)} decode docs, {len(verdicts)} rule verdicts")
    extraction_fixture()


def extraction_fixture():
    """Files of every format and what extract_bytes makes of them (text, kind, member, locations)."""
    import base64
    import zipfile

    from datashield_local.engine.extract import extract_bytes
    from tests.test_extract import docx_bytes, pdf_bytes, xlsx_bytes, zip_bytes

    card = "4111 1111 1111 1111"
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    docx_empty = zip_bytes({"word/document.xml":
                            f'<w:document xmlns:w="{w}"><w:body><w:p><w:pPr/><w:r><w:t>first &amp; one</w:t></w:r></w:p>'
                            f'<w:p/><w:p><w:r><w:t xml:space="preserve">card </w:t></w:r><w:r><w:tab/><w:t>{card}</w:t>'
                            f'</w:r></w:p></w:body></w:document>'})
    files = {
        "notes.txt": f"line one\nPAN ABCPV1234K\ncard {card}\n".encode(),
        "utf16.txt": "naam राहुल\ncard 4111 1111 1111 1111".encode("utf-16"),
        "page.html": b"<html><head><style>p{}</style><script>var x=1</script></head><body><p>Mail rahul@example.in &amp; co</p></body></html>",
        ".env": b"DB_PASSWORD=Summer@2024\nAWS_ACCESS_KEY_ID=AKIAIOSFODNN7ABCDEFG\n",
        "report.docx": docx_bytes(["Employee: Rahul Verma", f"Card {card}", "Aadhaar 2345 6789 0125"]),
        "empty-paras.docx": docx_empty,
        "pay.xlsx": xlsx_bytes([["name", "card", "cvv"], ["Rahul Verma", card, "123"]]),
        "scan.pdf": pdf_bytes(["Name Rahul Verma", f"Card {card} cvv 123"]),
        "bundle.zip": zip_bytes({"hr/salaries.xlsx": xlsx_bytes([["Anita", "5555 5555 5555 4444"]], "Q3"),
                                 "readme.txt": "nothing here", "deep/inner.zip": zip_bytes({"secret.env": "API_KEY=sk_" + "live_abcdefghijklmnopqrstuvwx"}),  # fake key split so GitHub secret scanning skips it
                                 "photo.png": b"\x89PNG\r\n\x1a\n" + bytes(200)}),
        "stored.zip": zip_bytes({"a.txt": f"card {card}"}, compress=zipfile.ZIP_STORED),
        "bomb.zip": zip_bytes({"big.txt": "0" * 2_000_000}),
        "blob.bin": bytes(range(256)) * 8,
        "not-a.zip": b"PK\x03\x04garbage",
    }
    rows = []
    for name, data in files.items():
        ex = extract_bytes(name, data)
        segs = []
        for s in ex.segments:
            probes = sorted({0, len(s.text) // 2, max(0, len(s.text) - 1), *(m.start() for m in __import__("re").finditer(r"\d{4} \d{4}", s.text))})
            segs.append({"text": s.text, "kind": s.kind, "member": s.member, "page": s.page,
                         "locs": [[p, s.locate(p)] for p in probes]})
        rows.append({"name": name, "b64": base64.b64encode(data).decode(), "segments": segs,
                     "skipped": ex.skipped, "truncated": ex.truncated})
    (OUT / "extract.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"{OUT / 'extract.json'}: {len(rows)} files")


if __name__ == "__main__":
    main()
