import validators as V
from common import read_jsonl


def test_checksums():
    assert V.is_valid("CREDIT_CARD", "4111 1111 1111 1111")
    assert not V.is_valid("CREDIT_CARD", "4111 1111 1111 1112")
    assert not V.is_valid("CREDIT_CARD", "XXXX-XXXX-XXXX-1234")
    assert not V.is_valid("AADHAAR", "0123 4567 8901")


def test_formats():
    assert V.is_valid("PAN", "ABCPE1234F")
    assert not V.is_valid("PAN", "ABCXE1234F")  # X is not a holder type
    assert V.is_valid("IP_ADDRESS", "10.0.3.17")
    assert not V.is_valid("IP_ADDRESS", "127.0.0.1")
    assert not V.is_valid("IP_ADDRESS", "4.12.3.456")
    assert not V.is_valid("PHONE", "1800-123-4567")
    assert V.is_valid("PHONE", "9876543210")
    assert not V.is_valid("PHONE", "1234567890")
    assert not V.is_valid("API_KEY", "AKIAXXXXXXXXXXXXXXXX")
    assert V.is_valid("PERSON", "anything")


def test_rescue_adds_missed_key_once():
    text = "key AKIAIOSFODNN7ABCDEFG and again"
    out = V.rescue(text, [])
    assert [s["value"] for s in out] == ["AKIAIOSFODNN7ABCDEFG"]
    assert V.rescue(text, out) == out
    assert V.rescue("AKIAXXXXXXXXXXXXXXXX", []) == []


def test_no_gold_span_rejected():
    rows = read_jsonl("data/v1/test.jsonl") + read_jsonl("data/v1/val.jsonl")
    bad = [(s["label"], r["text"][s["start"]:s["end"]]) for r in rows for s in r["spans"]
           if not V.is_valid(s["label"], r["text"][s["start"]:s["end"]])]
    assert bad == []


def test_rescue_overrides_wrong_label():
    text = "pass AKIAIOSFODNN7ABCDEFG end"
    wrong = [{"start": 5, "end": 15, "label": "PASSWORD", "value": text[5:15], "score": 0.6}]
    out = V.rescue(text, wrong)
    assert [(s["label"], s["value"]) for s in out] == [("API_KEY", "AKIAIOSFODNN7ABCDEFG")]


def test_financial_rules():
    assert V.is_valid("UPI_ID", "rahul.v92@okhdfcbank") and V.is_valid("UPI_ID", "9876543210@ybl")
    assert not V.is_valid("UPI_ID", "rahul@gmail.com")  # an email, not a UPI handle
    assert V.is_valid("CVV", "123") and V.is_valid("CVV", "1234") and not V.is_valid("CVV", "12345")
    assert V.is_valid("CARD_EXPIRY", "09/27") and V.is_valid("CARD_EXPIRY", "09/2027")
    assert not V.is_valid("CARD_EXPIRY", "13/27")
    assert V.is_valid("OTP_PIN", "482913") and not V.is_valid("OTP_PIN", "48A913")
    assert V.is_valid("BANK_ACCOUNT", "GB82 WEST 1234 5698 7654 32")
    assert not V.is_valid("BANK_ACCOUNT", "GB82 WEST 1234 5698 7654 31")  # IBAN check digits


def test_credential_rules():
    assert V.is_valid("USERNAME", "rahul.verma92") and not V.is_valid("USERNAME", "rahul verma")
    assert not V.is_valid("AUTH_TOKEN", "${ACCESS_TOKEN}") and not V.is_valid("AUTH_TOKEN", "<token>")
    assert V.is_valid("CONNECTION_STRING", "postgres://app:S3cret!@db.internal:5432/x")
    assert V.is_valid("CONNECTION_STRING", "Server=db;Database=x;User Id=app;Password=Xy12!;")
    assert not V.is_valid("CONNECTION_STRING", "postgres://localhost:5432/x")
    assert not V.is_valid("CONNECTION_STRING", "postgres://${DB_USER}:${DB_PASSWORD}@db/x")
    assert V.is_valid("CONNECTION_STRING", "mysql://u:$t2Ccy@h/db")  # a real password may start with $
    pem = "-----BEGIN EC PRIVATE KEY-----\nMHcCAQEE\n-----END EC PRIVATE KEY-----"
    assert V.is_valid("PRIVATE_KEY", pem) and V.is_valid("PRIVATE_KEY", pem.replace("\n", "\\n"))
    assert not V.is_valid("PRIVATE_KEY", pem.replace("PRIVATE", "PUBLIC"))


def test_rescue_labels_by_pattern():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.c2lnbmF0dXJlLWJ5dGVzLWhlcmU"
    pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADAN\n-----END PRIVATE KEY-----"
    text = f"Bearer {jwt}\nkey:\n{pem}\nDB postgres://app:S3cret!@db.internal/x. ghp_{'a' * 36}"
    out = [(s["label"], s["value"]) for s in V.rescue(text, [])]
    assert out == [("AUTH_TOKEN", jwt), ("PRIVATE_KEY", pem),
                   ("CONNECTION_STRING", "postgres://app:S3cret!@db.internal/x"), ("API_KEY", "ghp_" + "a" * 36)]


def test_rescue_replaces_nested_password():
    text = "url postgres://app:S3cret!@db.internal:5432/x ok"
    nested = [{"start": 19, "end": 26, "label": "PASSWORD", "value": "S3cret!", "score": 0.9}]
    assert [s["label"] for s in V.rescue(text, nested)] == ["CONNECTION_STRING"]


def test_no_v2_gold_span_rejected():
    rows = read_jsonl("data/v2/test.jsonl") + read_jsonl("data/v2/val.jsonl")
    bad = [(s["label"], r["text"][s["start"]:s["end"]]) for r in rows for s in r["spans"]
           if not V.is_valid(s["label"], r["text"][s["start"]:s["end"]])]
    assert bad == []
