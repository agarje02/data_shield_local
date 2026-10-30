import json
import shutil

import pytest
from fastapi.testclient import TestClient

from datashield_local.engine.__main__ import build
from datashield_local.engine.audit import clean_context

TEXT = "Rahul paid with 4111 1111 1111 1111 cvv 123. Best before 09/27."


class StubPredictor:
    """Finds fixed strings, so the API can be tested without loading the model."""
    backend, device, thresholds = "stub", "cpu", {}
    calls = 0

    def predict(self, texts, **kwargs):
        StubPredictor.calls += 1
        t, out = texts[0], []
        for label, val in (("PERSON", "Rahul"), ("CREDIT_CARD", "4111 1111 1111 1111"), ("CVV", "123"),
                           ("CARD_EXPIRY", "09/27")):
            i = t.find(val)
            if i >= 0:
                out.append({"start": i, "end": i + len(val), "label": label, "value": val, "score": 0.9})
        return [out]


@pytest.fixture()
def env(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    shutil.copy("configs/labels_v2.yaml", model / "labels.yaml")
    app, engine, ddir = build(model, db=tmp_path / "fb.db", ddir=tmp_path / "data", port=8765,
                              predictor=StubPredictor())
    token = json.loads((ddir / "engine_secret.json").read_text())["token"]
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    return client, {"Authorization": f"Bearer {token}"}, engine, ddir


def test_scan_applies_policy_and_negative_context(env):
    client, auth, _, _ = env
    r = client.post("/v1/scan", json={"text": TEXT, "source": "test"}, headers=auth).json()
    assert [s["label"] for s in r["spans"]] == ["PERSON", "CREDIT_CARD", "CVV"]   # "best before" dropped
    assert r["suppressed"][0]["label"] == "CARD_EXPIRY"
    a = r["assessment"]
    assert (a["risk"], a["action"]) == ("critical", "block")
    assert "card_kit" in [c["name"] for c in a["combos"]]
    assert "[CREDIT_CARD]" in r["masked"] and "4111" not in r["masked"]


def test_token_required_for_v1_but_not_health(env):
    client, auth, _, _ = env
    assert client.post("/v1/scan", json={"text": TEXT}).status_code == 401
    assert client.post("/v1/scan", json={"text": TEXT}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/v1/health").json()["status"] == "ok"


def test_foreign_origin_and_host_refused(env):
    client, auth, _, _ = env
    evil = {"Origin": "https://evil.example"}
    assert client.post("/api/scan", json={"text": TEXT}, headers=evil).status_code == 403
    assert client.post("/v1/scan", json={"text": TEXT}, headers={**auth, **evil}).status_code == 403
    # DNS rebinding: a foreign hostname resolving to 127.0.0.1
    assert client.get("/v1/health", headers={"Host": "attacker.example:8765"}).status_code == 403
    # the review page itself (same origin) still works
    own = {"Origin": "http://127.0.0.1:8765"}
    assert client.post("/api/scan", json={"text": TEXT}, headers=own).status_code == 200


def test_audit_has_hashes_not_values(env):
    client, auth, _, ddir = env
    client.post("/v1/scan", json={"text": TEXT, "context": {"url": "https://chat.example.com/c/123?q=secret"}},
                headers=auth)
    raw = (ddir / "audit" / "audit.jsonl").read_text()
    assert "4111" not in raw and "Rahul" not in raw and "secret" not in raw
    ev = json.loads(raw.splitlines()[-1])
    assert ev["context"] == {"host": "chat.example.com"}
    assert ev["risk"] == "critical" and all(len(f["h"]) == 16 for f in ev["findings"])


def test_cache_skips_the_model_on_repeat(env):
    client, auth, _, _ = env
    before = StubPredictor.calls
    a = client.post("/v1/scan", json={"text": TEXT + " once"}, headers=auth).json()
    b = client.post("/v1/scan", json={"text": TEXT + " once"}, headers=auth).json()
    assert StubPredictor.calls == before + 1 and (a["cached"], b["cached"]) == (False, True)
    assert a["spans"] == b["spans"]


def test_clean_context_drops_unknown_keys():
    assert clean_context({"url": "https://a.b/x", "cookie": "s=1", "app": "chrome"}) == {"host": "a.b", "app": "chrome"}
