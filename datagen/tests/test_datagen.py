import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common import load_labels  # noqa: E402
from datagen import components as C  # noqa: E402
from datagen.build import Composer, Doc, split_components  # noqa: E402
from datagen.values import LookalikeValues, SensitiveValues, luhn_valid, verhoeff_valid  # noqa: E402

LABELS = load_labels(ROOT / "configs" / "labels_v2.yaml")
CFG = {"version": "t", "neg_sentence_frac": 0.35, "long_doc_frac": 0.05, "clean_doc_frac": 0.45, "combo_frac": 0.3,
       "style_weights": {s: 1 for s in Composer.STYLES}}


def test_every_label_has_cues_and_keys():
    for lab in LABELS:
        assert len(C.POS_CUES[lab]) >= 3 and len(C.POS_KEYS[lab]) >= 3, lab


def test_every_lookalike_method_exists():
    lk = LookalikeValues(1)
    for method in set(C.NEG_CUES) | set(C.NEG_KEYS.values()):
        assert isinstance(getattr(lk, method)(), str), method


def test_checksums_valid():
    sv = SensitiveValues(3)
    for _ in range(200):
        assert verhoeff_valid(re.sub(r"\D", "", sv.AADHAAR()))
        assert luhn_valid(re.sub(r"\D", "", sv.CREDIT_CARD()))


def test_components_disjoint_across_splits():
    sp = split_components(seed=7, frac=0.15)
    for name in ("frames", "neutral", "neg_keys"):
        sets = [set(sp[s][name]) for s in ("train", "val", "test")]
        assert not (sets[0] & sets[1]) and not (sets[0] & sets[2]) and not (sets[1] & sets[2]), name
    for name in ("pos_cues", "neg_cues"):
        for g in sp["train"][name]:
            shared = set(C.SHARED.get(name, {}).get(g, []))
            tr, va, te = (set(sp[s][name][g]) - shared for s in ("train", "val", "test"))
            assert not (tr & te) and not (tr & va), (name, g)
            for s in ("train", "val", "test"):
                assert shared <= set(sp[s][name][g]), (name, g, s)  # key contrasts in every split
    shared = set(C.SHARED["combos"])
    tr, va, te = (set(sp[s]["combos"]) - shared for s in ("train", "val", "test"))
    assert not (tr & te) and not (tr & va)
    assert all(shared <= set(sp[s]["combos"]) for s in ("train", "val", "test"))
    # a shared item must not also sit in the disjoint pools (it would be counted twice)
    for name in ("pos_cues", "neg_cues"):
        for g, items in C.SHARED[name].items():
            pool = C.POS_CUES[g] if name == "pos_cues" else C.NEG_CUES[g]
            assert not set(items) & set(pool), (name, g)
    assert not shared & set(C.COMBOS)


def test_documents_have_exact_offsets_and_clean_docs_have_no_spans():
    sp = split_components(seed=7, frac=0.15)
    comp = Composer(sp["train"], LABELS, seed=11, cfg=CFG)
    for i in range(400):
        positive = i % 2 == 0
        doc, style = comp.document(positive)
        text = doc.text
        for s in doc.spans:
            assert text[s["start"]:s["end"]] == s["value"], (style, s)
            assert s["value"] == s["value"].strip()
        if not positive:
            assert doc.spans == [], style


def test_combos_give_one_span_per_slot():
    import validators
    sp = split_components(seed=7, frac=0.15)
    comp = Composer(sp["train"], LABELS, seed=5, cfg={**CFG, "combo_frac": 1.0})
    doc = Doc()
    for _ in range(50):
        comp.cue_sentence(doc, True)
        doc.add(" ")
    assert comp.used["combo"] == 50
    for s in doc.spans:
        assert doc.text[s["start"]:s["end"]] == s["value"]
        assert validators.is_valid(s["label"], s["value"]), s


def test_new_values_pass_their_rules_and_lookalikes_do_not():
    import validators
    sv, lk = SensitiveValues(9), LookalikeValues(9)
    for label in ("UPI_ID", "CVV", "CARD_EXPIRY", "OTP_PIN", "USERNAME", "AUTH_TOKEN", "PRIVATE_KEY",
                  "CONNECTION_STRING", "BANK_ACCOUNT"):
        for _ in range(200):
            v = getattr(sv, label)()
            assert validators.is_valid(label, v), (label, v)
    for _ in range(100):
        assert not validators.is_valid("PRIVATE_KEY", lk.public_key())
        assert not validators.is_valid("PRIVATE_KEY", lk.certificate())
        assert not validators.is_valid("CONNECTION_STRING", lk.conn_no_creds())
        assert not validators.is_valid("AUTH_TOKEN", lk.placeholder_token())


def test_records_escape_multiline_values():
    sp = split_components(seed=7, frac=0.15)
    comp = Composer(sp["train"], ["PRIVATE_KEY"], seed=3, cfg=CFG)
    for fmt in ("env", "json", "csv"):
        doc = Doc()
        comp.record(doc, 1, fmt=fmt)
        (s,) = doc.spans
        assert "\n" not in s["value"] and "\\n" in s["value"], fmt


def test_key_styles_vary_and_keep_offsets():
    sp = split_components(seed=7, frac=0.15)
    comp = Composer(sp["train"], LABELS, seed=4, cfg={**CFG, "key_style_keep": 0.0, "inline_records": True})
    keys = set()
    for fmt in ("kv", "json", "inline", "csv"):
        for _ in range(30):
            doc = Doc()
            comp.record(doc, 2, fmt=fmt)
            assert all(doc.text[s["start"]:s["end"]] == s["value"] for s in doc.spans), fmt
            keys.update(re.findall(r"([A-Za-z][\w .#]*?)(?:: |=| - )", doc.text))
    assert any(k.isupper() and "_" in k for k in keys) and any(" " in k and k[0].isupper() for k in keys)
    same = Composer(sp["train"], LABELS, seed=4, cfg=CFG)  # default: keys as written
    assert same.key_style("bank_account", "kv") == "bank_account"
