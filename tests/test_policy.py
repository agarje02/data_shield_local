from common import load_labels
from datashield_local.policy import Policy

P = Policy()


def S(label):
    return {"start": 0, "end": 1, "label": label}


def test_every_model_label_has_a_level():
    for lab in load_labels("configs/labels_v2.yaml"):
        assert lab in P.label_level, lab


def test_cvv_outranks_aadhaar():
    assert P.levels[P.level("CVV")]["score"] > P.levels[P.level("AADHAAR")]["score"]


def test_document_risk_is_highest_finding():
    a = P.assess([S("PERSON"), S("PHONE")])
    assert (a["risk"], a["action"]) == ("medium", "warn")
    assert [f["severity"] for f in a["findings"]] == ["low", "medium"]
    assert P.assess([])["risk"] == "none"


def test_combos_escalate():
    a = P.assess([S("CREDIT_CARD"), S("CARD_EXPIRY")])
    assert a["risk"] == "critical" and [c["name"] for c in a["combos"]] == ["card_kit"]
    a = P.assess([S("PERSON"), S("AADHAAR"), S("DATE_OF_BIRTH")])
    assert a["risk"] == "critical" and "identity_kit" in [c["name"] for c in a["combos"]]
    # identity kit needs a personal detail too, not just name + ID
    assert "identity_kit" not in [c["name"] for c in P.assess([S("PERSON"), S("AADHAAR")])["combos"]]
