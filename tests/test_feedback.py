import json

from datashield_local.feedback import FeedbackStore, diff_spans


def S(a, b, label):
    return {"start": a, "end": b, "label": label}


def test_diff_kinds():
    pred = [S(0, 5, "PERSON"), S(10, 20, "PAN"), S(30, 40, "EMAIL"), S(50, 60, "PHONE")]
    corr = [S(0, 5, "PERSON"), S(30, 40, "PERSON"), S(50, 58, "PHONE"), S(70, 75, "AADHAAR")]
    d = diff_spans(pred, corr)
    assert d["correct"] == ["PERSON"]
    assert d["relabel"] == ["EMAIL->PERSON"]
    assert d["boundary"] == ["PHONE"]
    assert d["missed"] == ["AADHAAR"]
    assert d["wrong"] == ["PAN"]


def test_store_roundtrip(tmp_path):
    store = FeedbackStore(tmp_path / "fb.db")
    text = "Call Rahul on 9876543210"
    store.add(text, [S(5, 10, "PERSON")], [S(5, 10, "PERSON"), S(14, 24, "PHONE")])
    store.add("nothing here", [S(0, 7, "PAN")], [])
    st = store.stats()
    assert st["docs"] == 2 and st["clean_docs"] == 1
    assert st["totals"]["missed"] == 1 and st["totals"]["wrong"] == 1
    lines = store.export_jsonl().strip().splitlines()
    assert len(lines) == 2 and '"value": "9876543210"' in lines[0]


def test_golden_export_drops_rule_failures(tmp_path):
    from collections import Counter
    store = FeedbackStore(tmp_path / "fb.db")
    text = "Aadhaar 2345 6789 0124 and 2345 6789 0125"
    spans = [{"start": 8, "end": 22, "label": "AADHAAR"}, {"start": 27, "end": 41, "label": "AADHAAR"}]
    store.add(text, predicted=[], corrected=spans)
    dropped = Counter()
    (row,) = [json.loads(l) for l in store.export_jsonl(golden=True, dropped=dropped).splitlines()]
    assert [text[s["start"]:s["end"]] for s in row["spans"]] == ["2345 6789 0125"]
    assert dropped == {"AADHAAR": 1}
