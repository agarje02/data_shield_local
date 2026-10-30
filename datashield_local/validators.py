"""Rule layer that runs after the model: format and checksum checks.

The model decides *where* something sensitive is from context; these rules
decide whether the value can actually be that type.

  verify  - drop a predicted span whose value cannot be its label
            (Aadhaar fails Verhoeff, card fails Luhn, PAN has an impossible
            holder-type letter, IP has an octet > 255, ...).
  rescue  - add spans for secret formats that are unambiguous on their own
            (AKIA..., ghp_..., sk-ant-..., JWTs, PEM private keys, URIs with
            user:password) when the model missed them.

Labels without a rule (PERSON, ADDRESS, DATE_OF_BIRTH, PASSWORD) pass through.
"""
import ipaddress
import re

from datashield_local.checksums import iban_valid, luhn_valid, verhoeff_valid

_PAN = re.compile(r"[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z]")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
# Indian toll-free / shared-cost numbers belong to businesses, not people.
_TOLL_FREE = re.compile(r"^(?:\+?91[- ]?)?0?18[06]0")
_PHONE_EXT = re.compile(r"\s*(?:x|ext\.?)\s*\d+$", re.I)
# Placeholders shaped like keys: AKIAXXXXXXXXXXXXXXXX, sk-xxxxxxxx, ...EXAMPLE
_PLACEHOLDER = re.compile(r"([xX*0])\1{5,}|EXAMPLE|YOUR_|REPLACE", re.I)
_TEMPLATE = re.compile(r"\$\{\w+\}|\$[A-Z_][A-Z0-9_]*|\{\{.*?\}\}|<[^>]+>")  # ${DB_PASSWORD}, $TOKEN, {{t}}, <pw>
_UPI = re.compile(r"[\w.-]{2,256}@[A-Za-z][A-Za-z0-9]{1,63}")  # no dot after @, unlike an email
_EXPIRY = re.compile(r"(0[1-9]|1[0-2])\s?[/-]\s?(\d{2}|20\d{2})")
_PEM = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[\s\S]+?-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----")
_URI_CREDS = re.compile(r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|rediss?|amqps?|mssql)://"
                        # stops at CSV delimiters, no trailing punctuation
                        r"([^\s:/@|;,]*):([^\s@/|;,]+)@[^\s'\"<>|;,]*[^\s'\"<>|;,.:?!)\]]")
_ADO_PASSWORD = re.compile(r"(?:Password|Pwd)=([^;]+);", re.I)

# Formats specific enough to trust without the model: (label, pattern).
SECRET_PATTERNS = [("API_KEY", re.compile(p)) for p in (
    r"\bAKIA[A-Z0-9]{16}\b",
    r"\bgh[pousr]_[A-Za-z0-9]{36}\b",
    r"\bgithub_pat_[A-Za-z0-9]{22}_[A-Za-z0-9]{59}\b",
    r"\bsk-ant-api03-[A-Za-z0-9_-]{80,}",
    r"\bsk-proj-[A-Za-z0-9_-]{40,}",
    r"\bxox[bpas]-\d{8,13}-\d{8,13}-[A-Za-z0-9]{20,}\b",
    r"\bglpat-[A-Za-z0-9_-]{20}\b",
    r"\bhf_[A-Za-z0-9]{34}\b",
    r"\b(?:sk|rk)_live_[A-Za-z0-9]{24,}\b",
    r"\brzp_live_[A-Za-z0-9]{14}\b",
    r"\bAIza[A-Za-z0-9_-]{35}\b",
)] + [
    ("AUTH_TOKEN", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{16,}")),
    ("PRIVATE_KEY", _PEM),
    ("CONNECTION_STRING", _URI_CREDS),
]


def _digits(value):
    return re.sub(r"\D", "", value)


def _aadhaar(v):
    d = _digits(v)
    return len(d) == 12 and d[0] not in "01" and verhoeff_valid(d)


def _card(v):
    d = _digits(v)
    return 13 <= len(d) <= 19 and luhn_valid(d)


def _pan(v):
    return bool(_PAN.fullmatch(v.upper()))


def _bank(v):
    if v[:2].isalpha():
        return iban_valid(v)
    return v.replace(" ", "").replace("-", "").isdigit() and 9 <= len(_digits(v)) <= 18


def _phone(v):
    s = v.strip()
    if _TOLL_FREE.match(s):
        return False
    if s.isdigit():
        # bare digit runs: only shapes a real number can have (Indian mobile 6-9xxxxxxxxx,
        # 0/91 prefixed, US NANP with 2-9 area and exchange); others are AWB / order / ref numbers
        if len(s) == 10:
            return s[0] in "6789" or (s[0] in "2345" and s[3] in "23456789")
        if len(s) == 11:
            return (s[0] == "0" and s[1] in "6789") or s[0] == "1"
        if len(s) == 12:
            return s.startswith("91") and s[2] in "6789"
        return False
    return 7 <= len(_digits(_PHONE_EXT.sub("", s))) <= 15


def _ip(v):
    try:
        ip = ipaddress.ip_address(v.strip("[]"))
    except ValueError:
        return False
    return not (ip.is_loopback or ip.is_unspecified)


def _email(v):
    return bool(_EMAIL.fullmatch(v))


def _api_key(v):
    return len(v) >= 16 and not _PLACEHOLDER.search(v)


def _secret_ok(v):
    return not _PLACEHOLDER.search(v) and not _TEMPLATE.fullmatch(v.strip())


def _upi(v):
    return bool(_UPI.fullmatch(v))


def _cvv(v):
    return v.isdigit() and len(v) in (3, 4)


def _expiry(v):
    return bool(_EXPIRY.fullmatch(v.strip()))


def _otp_pin(v):
    return v.isdigit() and 4 <= len(v) <= 8


def _username(v):
    return 3 <= len(v) <= 64 and not any(c.isspace() for c in v)


def _auth_token(v):
    return len(v) >= 20 and _secret_ok(v)


def _private_key(v):
    # in a .env / JSON value the newlines are escaped
    return bool(_PEM.fullmatch(v.strip().replace("\\n", "\n")))


def _connection_string(v):
    m = _URI_CREDS.fullmatch(v.strip()) or _ADO_PASSWORD.search(v)
    pw = m and m.group(m.lastindex)
    return bool(pw) and _secret_ok(pw)


RULES = {
    "AADHAAR": _aadhaar,
    "CREDIT_CARD": _card,
    "PAN": _pan,
    "BANK_ACCOUNT": _bank,
    "PHONE": _phone,
    "IP_ADDRESS": _ip,
    "EMAIL": _email,
    "API_KEY": _api_key,
    "UPI_ID": _upi,
    "CVV": _cvv,
    "CARD_EXPIRY": _expiry,
    "OTP_PIN": _otp_pin,
    "USERNAME": _username,
    "AUTH_TOKEN": _auth_token,
    "PRIVATE_KEY": _private_key,
    "CONNECTION_STRING": _connection_string,
}


def is_valid(label, value):
    rule = RULES.get(label)
    return rule is None or rule(value)


def verify(spans):
    return [sp for sp in spans if is_valid(sp["label"], sp["value"])]


def rescue(text, spans):
    """Make unambiguous secret formats spans of their pattern's label.

    The rule wins over overlapping model spans (e.g. AKIA... tagged PASSWORD, a
    key cut short, a PASSWORD inside a connection string); a model span that
    already matches exactly is kept as is."""
    out = list(spans)
    for label, pat in SECRET_PATTERNS:
        for m in pat.finditer(text):
            if not is_valid(label, m.group()):
                continue
            if any((sp["start"], sp["end"], sp["label"]) == (m.start(), m.end(), label) for sp in out):
                continue
            out = [sp for sp in out if not (sp["start"] < m.end() and m.start() < sp["end"])]
            out.append({"start": m.start(), "end": m.end(), "label": label,
                        "value": m.group(), "score": 1.0, "source": "rule"})
    return sorted(out, key=lambda s: s["start"])


def apply(text, spans):
    return rescue(text, verify(spans))
