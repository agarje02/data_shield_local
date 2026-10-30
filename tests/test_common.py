import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))

from common import (bio_entities, build_label_maps, load_labels, mask_text,  # noqa: E402
                    spans_to_token_labels, tokens_to_spans)
import synth_generator as sg  # noqa: E402

LABELS = load_labels(ROOT / "configs" / "labels.yaml")
L2I, I2L = build_label_maps(LABELS)


def toy_tokenize(text):
    """Word / punctuation pieces with offsets, plus fake special tokens at both ends."""
    toks = [(m.start(), m.end()) for m in re.finditer(r"\w+|[^\w\s]", text)]
    offsets = [(0, 0)] + toks + [(0, 0)]
    seq_ids = [None] + [0] * len(toks) + [None]
    return offsets, seq_ids


def roundtrip(text, spans):
    offsets, seq_ids = toy_tokenize(text)
    lab = spans_to_token_labels(offsets, seq_ids, spans, L2I)
    toks = [(s, e, l, 1.0) for (s, e), l in zip(offsets, lab) if l != -100]
    return lab, tokens_to_spans(text, toks, I2L)


def sp(text, value, label):
    s = text.index(value)
    return {"start": s, "end": s + len(value), "label": label, "value": value}


def test_roundtrip_multi_token_spans():
    text = "Call Rahul Sharma at rahul.s@gmail.com, PAN ABCPD1234E."
    gold = [sp(text, "Rahul Sharma", "PERSON"), sp(text, "rahul.s@gmail.com", "EMAIL"),
            sp(text, "ABCPD1234E", "PAN")]
    lab, got = roundtrip(text, gold)
    assert lab[0] == -100 and lab[-1] == -100
    assert [(g["start"], g["end"], g["label"]) for g in got] == [(g["start"], g["end"], g["label"]) for g in gold]


def test_adjacent_same_label_spans_stay_separate():
    text = "Rahul Priya"
    gold = [sp(text, "Rahul", "PERSON"), sp(text, "Priya", "PERSON")]
    _, got = roundtrip(text, gold)
    assert len(got) == 2


def test_trailing_punctuation_trimmed_but_not_for_passwords():
    text = "pwd: Abc!"
    offsets = [(5, 9)]
    toks = [(5, 9, L2I["B-PASSWORD"], 0.9)]
    assert tokens_to_spans(text, toks, I2L)[0]["value"] == "Abc!"
    toks = [(5, 9, L2I["B-PERSON"], 0.9)]
    assert tokens_to_spans(text, toks, I2L)[0]["value"] == "Abc"
    assert offsets  # keep linters quiet


def test_bio_entities_lenient_i_start():
    tags = ["O", "I-EMAIL", "I-EMAIL", "O", "B-PAN", "I-PAN", "B-PAN"]
    assert bio_entities(tags) == [("EMAIL", 1, 3), ("PAN", 4, 6), ("PAN", 6, 7)]


def test_mask_text():
    text = "mail a@b.co now"
    assert mask_text(text, [sp(text, "a@b.co", "EMAIL")]) == "mail [EMAIL] now"


def test_checksums():
    v = sg.Values(1)
    for _ in range(200):
        a = v.AADHAAR().replace(" ", "").replace("-", "")
        assert len(a) == 12 and sg.verhoeff_valid(a)
        c = re.sub(r"\D", "", v.CREDIT_CARD())
        assert sg.luhn_valid(c)
    assert sg.luhn_valid("4111111111111111")


def test_render_offsets_exact():
    v = sg.Values(7)
    for tid, _, tmpl in sg.TEMPLATES:
        text, spans = sg.render(tmpl, v, LABELS)
        for s in spans:
            assert text[s["start"]:s["end"]] == s["value"], tid
        assert "{n:" not in text, tid


def test_template_split_covers_all_labels():
    splits = sg.split_templates(sg.TEMPLATES, LABELS, seed=13)
    ids = [t[0] for ts in splits.values() for t in ts]
    assert len(ids) == len(set(ids)) == len(sg.TEMPLATES)


# ---------------------------------------------------------------- word-level decoding

from common import aggregate_words, word_ids  # noqa: E402


def probs_for(tag, conf=0.9):
    p = [0.0] * len(I2L)
    p[L2I[tag]] = conf
    p[L2I["O"]] += 1 - conf
    return p


def test_word_ids_split_on_space_and_delimiters():
    text = "897 4;116 x"
    toks = [(0, 2), (2, 3), (3, 5), (5, 6), (6, 9), (9, 11)]   # "89" "7" " 4" ";" "116" " x"
    assert word_ids(text, toks) == [0, 0, 1, 2, 3, 4]


def test_aggregate_words_gives_one_label_per_word():
    text = "id 131590423307 end"
    # sub-tokens of one number disagree: first piece BANK_ACCOUNT, second PHONE (stronger overall BANK)
    toks = [(0, 2, probs_for("O")), (3, 6, probs_for("B-BANK_ACCOUNT", 0.9)),
            (6, 15, probs_for("I-PHONE", 0.6)), (15, 19, probs_for("O"))]
    words = aggregate_words(text, toks, I2L)
    spans = tokens_to_spans(text, words, I2L)
    assert [(sp["label"], sp["value"]) for sp in spans] == [("BANK_ACCOUNT", "131590423307")]


def test_spans_do_not_cross_newlines():
    text = "77003 29536\n- Siddharth"
    toks = [(0, 5, L2I["B-PHONE"], 0.9), (6, 11, L2I["I-PHONE"], 0.9), (14, 23, L2I["I-PHONE"], 0.9)]
    spans = tokens_to_spans(text, toks, I2L)
    assert spans[0]["value"] == "77003 29536" and len(spans) == 2


def test_phone_keeps_leading_bracket():
    text = "Mobile: (+91) 722-031-1491"
    toks = [(8, 26, L2I["B-PHONE"], 0.9)]
    assert tokens_to_spans(text, toks, I2L)[0]["value"] == "(+91) 722-031-1491"
    toks = [(8, 26, L2I["B-EMAIL"], 0.9)]
    assert tokens_to_spans(text, toks, I2L)[0]["value"].startswith("+91")


def test_newline_inside_token_offset_still_splits():
    # deberta-style offsets: the second name's first token starts AT the newline
    text = "Adam\nAyushman Tank"
    toks = [(0, 4, L2I["B-PERSON"], 0.9), (4, 13, L2I["I-PERSON"], 0.9), (13, 18, L2I["I-PERSON"], 0.9)]
    assert [sp["value"] for sp in tokens_to_spans(text, toks, I2L)] == ["Adam", "Ayushman Tank"]


def test_api_key_drops_sentence_full_stop():
    text = "token ghp_abc123XYZ."
    toks = [(6, 20, L2I["B-API_KEY"], 0.9)]
    assert tokens_to_spans(text, toks, I2L)[0]["value"] == "ghp_abc123XYZ"


def test_newline_inside_token_offset_still_splits():
    # deberta-style offsets: the second name's first token starts AT the newline
    text = "Adam\nAyushman Tank"
    toks = [(0, 4, L2I["B-PERSON"], 0.9), (4, 13, L2I["I-PERSON"], 0.9), (13, 18, L2I["I-PERSON"], 0.9)]
    assert [sp["value"] for sp in tokens_to_spans(text, toks, I2L)] == ["Adam", "Ayushman Tank"]


def test_api_key_drops_sentence_full_stop():
    text = "token ghp_abc123XYZ."
    toks = [(6, 20, L2I["B-API_KEY"], 0.9)]
    assert tokens_to_spans(text, toks, I2L)[0]["value"] == "ghp_abc123XYZ"


def test_password_drops_sentence_full_stop_but_keeps_symbols():
    text = "the password is E0VRXrd!. Next"
    toks = [(16, 25, L2I["B-PASSWORD"], 0.9)]
    assert tokens_to_spans(text, toks, I2L)[0]["value"] == "E0VRXrd!"
    text = "pwd=Abc.def done"
    toks = [(4, 11, L2I["B-PASSWORD"], 0.9)]
    assert tokens_to_spans(text, toks, I2L)[0]["value"] == "Abc.def"


def test_dob_extended_over_year():
    from common import repair_boundaries
    text = "born May 01, 1984 in Pune"
    sp = [{"start": 5, "end": 11, "label": "DATE_OF_BIRTH", "value": "May 01", "score": 0.9}]
    assert repair_boundaries(text, sp)[0]["value"] == "May 01, 1984"
    text = "DOB 14 October 1962."
    sp = [{"start": 4, "end": 14, "label": "DATE_OF_BIRTH", "value": "14 October", "score": 0.9}]
    assert repair_boundaries(text, sp)[0]["value"] == "14 October 1962"
    text = "DOB 12/03/1990, ok"
    sp = [{"start": 4, "end": 14, "label": "DATE_OF_BIRTH", "value": "12/03/1990", "score": 0.9}]
    assert repair_boundaries(text, sp)[0]["value"] == "12/03/1990"


def test_password_drops_separator_and_question_mark():
    text = "creds: rahul92:Discover%2021? next"
    toks = [(14, 29, L2I["B-PASSWORD"], 0.9)]  # ":Discover%2021?"
    assert tokens_to_spans(text, toks, I2L)[0]["value"] == "Discover%2021"
    text = "wifi pass Summer2024! ok"
    toks = [(10, 21, L2I["B-PASSWORD"], 0.9)]
    assert tokens_to_spans(text, toks, I2L)[0]["value"] == "Summer2024!"  # "!" is part of passwords


def test_user_pass_split_only_with_username_label():
    from common import repair_boundaries
    text = "creds: rahul92:Secret@1."
    sp = [{"start": 7, "end": 23, "label": "PASSWORD", "value": "rahul92:Secret@1", "score": 0.9}]
    out = repair_boundaries(text, [dict(s) for s in sp], labels={"USERNAME", "PASSWORD"})
    assert [(s["label"], s["value"], text[s["start"]:s["end"]]) for s in out] == [
        ("USERNAME", "rahul92", "rahul92"), ("PASSWORD", "Secret@1", "Secret@1")]
    assert repair_boundaries(text, [dict(s) for s in sp], labels={"PASSWORD"}) == sp  # v1 model: unchanged
