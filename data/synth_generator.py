"""Synthetic training data for the sensitive-data detector.

Templates contain two kinds of placeholders:
  {LABEL}     -> a sensitive value, recorded as a span (LABEL from labels.yaml)
  {n:filler}  -> a non-sensitive value (hard negative), NOT labelled

Offsets are recorded while the string is built, so every span is exact.
Templates are split disjointly across train/val/test, so test text structure
is never seen in training.

Usage:
  python data/synth_generator.py --out data/v0
"""
import argparse
import hashlib
import json
import random
import re
import string
import sys
import uuid
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import load_labels, write_jsonl  # noqa: E402

GENERATOR_VERSION = "0.1"
PLACEHOLDER = re.compile(r"\{(n:)?(\w+)\}")

# ----------------------------------------------------------------------------- templates
# (template_id, style, text). Keep each template's fixed wording distinct:
# leakage_check compares n-grams across splits.
TEMPLATES = [
    # --- emails
    ("em01", "email", "Hi team,\n\nPlease update the KYC record for {PERSON}. PAN: {PAN}, Aadhaar: {AADHAAR}. "
                      "You can reach them at {EMAIL} or {PHONE}.\n\nThanks,\n{PERSON}"),
    ("em02", "email", "Dear {PERSON},\nYour salary for {n:month} has been credited to account {BANK_ACCOUNT}. "
                      "For queries write to {EMAIL}.\nRegards, Payroll, {n:company}"),
    ("em03", "email", "Subject: Address change request\n\nHello, I recently moved. My new address is {ADDRESS}. "
                      "Please update it against customer {n:customer_ref}. Mobile: {PHONE}\n- {PERSON}"),
    ("em04", "email", "FYI the vendor login for the portal is {EMAIL} with password {PASSWORD}. "
                      "Don't share outside {n:company}."),
    ("em05", "email", "Good morning {PERSON}, attaching the onboarding form. Kindly confirm your date of birth "
                      "({DATE_OF_BIRTH}) and permanent address: {ADDRESS}."),
    ("em06", "email", "Hello support, my card {CREDIT_CARD} was charged twice for order {n:order_id}. "
                      "Name on card: {PERSON}. Please refund. Contact {PHONE}."),
    # --- chat
    ("ch01", "chat", "[{n:time}] {PERSON}: bro send me ur aadhaar pic\n[{n:time}] {PERSON}: its {AADHAAR}, dont lose it"),
    ("ch02", "chat", "{PERSON}: what's the wifi admin pwd?\n{PERSON}: {PASSWORD} — router is at {IP_ADDRESS}"),
    ("ch03", "chat", "hey can u pay the rent, my acc no is {BANK_ACCOUNT} ifsc {n:ifsc}. call me on {PHONE} if issue"),
    ("ch04", "chat", "user> here is my openai key {API_KEY} why does it say quota exceeded\nbot> Please rotate it."),
    ("ch05", "chat", "Customer {PERSON} (DOB {DATE_OF_BIRTH}) asked to block card {CREDIT_CARD} immediately."),
    ("ch06", "chat", "lol my email got hacked, it was {EMAIL} and the old pass was {PASSWORD}"),
    # --- forms
    ("fm01", "form", "Full Name: {PERSON}\nDate of Birth: {DATE_OF_BIRTH}\nPAN Number: {PAN}\nAadhaar No: {AADHAAR}\n"
                     "Mobile: {PHONE}\nEmail ID: {EMAIL}\nAddress: {ADDRESS}"),
    ("fm02", "form", "APPLICANT DETAILS | Name - {PERSON} | D.O.B - {DATE_OF_BIRTH} | Contact - {PHONE} | "
                     "Residence - {ADDRESS}"),
    ("fm03", "form", "Beneficiary: {PERSON}\nAccount Number: {BANK_ACCOUNT}\nIFSC: {n:ifsc}\nBranch: {n:city}\n"
                     "Amount: {n:amount}"),
    ("fm04", "form", "Patient name: {PERSON}   Born: {DATE_OF_BIRTH}   Phone: {PHONE}\nVisit date: {n:date}   "
                     "Dept: {n:department}"),
    ("fm05", "form", "Card holder: {PERSON}\nCard number: {CREDIT_CARD}\nExpiry: {n:expiry}\nBilling address: {ADDRESS}"),
    # --- logs
    ("lg01", "log", "{n:timestamp} INFO auth: login success user={EMAIL} src_ip={IP_ADDRESS} session={n:uuid}"),
    ("lg02", "log", "{n:timestamp} WARN payments: retry charge card={CREDIT_CARD} amount={n:amount} attempt=2"),
    ("lg03", "log", "{n:timestamp} DEBUG db: connect host={IP_ADDRESS} user=admin password={PASSWORD} db=orders"),
    ("lg04", "log", "{n:timestamp} ERROR kyc-service: validation failed for pan={PAN} aadhaar={AADHAAR} "
                    "trace={n:sha}"),
    ("lg05", "log", "{IP_ADDRESS} - - [{n:timestamp}] \"POST /api/v1/users HTTP/1.1\" 201 {n:int} "
                    "\"email={EMAIL}&phone={PHONE}\""),
    ("lg06", "log", "{n:timestamp} INFO outbound request Authorization: Bearer {API_KEY} -> {n:url} ({n:int} ms)"),
    # --- config / env / code
    ("cf01", "env", "# .env for {n:env}\nDATABASE_URL=postgres://app:{PASSWORD}@{IP_ADDRESS}:5432/main\n"
                    "OPENAI_API_KEY={API_KEY}\nLOG_LEVEL=info"),
    ("cf02", "env", "AWS_REGION=ap-south-1\nAWS_ACCESS_KEY_ID={API_KEY}\nBUILD_SHA={n:sha}\nAPP_VERSION={n:version}"),
    ("cf03", "yaml", "smtp:\n  host: {IP_ADDRESS}\n  username: {EMAIL}\n  password: \"{PASSWORD}\"\n  port: {n:port}"),
    ("cf04", "code", "client = PaymentClient(\n    api_key=\"{API_KEY}\",\n    timeout={n:small_int},\n)\n"
                     "client.charge(card=\"{CREDIT_CARD}\", amount={n:small_int}00)"),
    ("cf05", "code", "curl -X POST {n:url} -H \"X-Api-Key: {API_KEY}\" -d '{{\"email\": \"{EMAIL}\"}}'"),
    ("cf06", "code", "def test_signup():\n    user = create_user(name=\"{PERSON}\", phone=\"{PHONE}\", dob=\"{DATE_OF_BIRTH}\")\n"
                     "    assert user.id"),
    # --- json / csv / sql
    ("js01", "json", "{{\"name\": \"{PERSON}\", \"email\": \"{EMAIL}\", \"phone\": \"{PHONE}\", \"pan\": \"{PAN}\", "
                     "\"order\": \"{n:order_id}\"}}"),
    ("js02", "json", "{{\"user_id\": \"{n:uuid}\", \"ip\": \"{IP_ADDRESS}\", \"card\": \"{CREDIT_CARD}\", "
                     "\"dob\": \"{DATE_OF_BIRTH}\"}}"),
    ("js03", "json", "{{\"beneficiary\": {{\"name\": \"{PERSON}\", \"account\": \"{BANK_ACCOUNT}\", \"ifsc\": \"{n:ifsc}\"}}, "
                     "\"amount\": \"{n:amount}\"}}"),
    ("cs01", "csv", "name,email,phone,city\n{PERSON},{EMAIL},{PHONE},{n:city}\n{PERSON},{EMAIL},{PHONE},{n:city}"),
    ("cs02", "csv", "emp_id;full_name;pan;aadhaar;bank_ac\n{n:emp_code};{PERSON};{PAN};{AADHAAR};{BANK_ACCOUNT}"),
    ("sq01", "sql", "INSERT INTO customers (name, email, dob, address) VALUES ('{PERSON}', '{EMAIL}', "
                    "'{DATE_OF_BIRTH}', '{ADDRESS}');"),
    ("sq02", "sql", "UPDATE accounts SET password_hash = NULL, temp_password = '{PASSWORD}' WHERE email = '{EMAIL}';"),
    # --- invoices / tickets / documents
    ("iv01", "invoice", "TAX INVOICE  No. {n:invoice_no}  Date: {n:date}\nBill to: {PERSON}, {ADDRESS}\n"
                        "PAN: {PAN}\nTotal payable: {n:amount}"),
    ("iv02", "invoice", "Receipt #{n:invoice_no}\nPaid by card {CREDIT_CARD} on {n:date}\nCustomer phone {PHONE}"),
    ("tk01", "ticket", "Ticket {n:ticket}: User {PERSON} cannot log in. They gave me their password ({PASSWORD}) "
                       "to test. Last login IP {IP_ADDRESS}."),
    ("tk02", "ticket", "Ticket {n:ticket} [P2] Refund to account {BANK_ACCOUNT} failed. Customer: {PERSON}, {EMAIL}."),
    ("tk03", "ticket", "Escalation {n:ticket}: caller {PERSON} verified with DOB {DATE_OF_BIRTH} and last 4 of "
                       "Aadhaar. Full Aadhaar on file: {AADHAAR}."),
    ("dc01", "document", "This rental agreement is made between {PERSON}, residing at {ADDRESS}, and {PERSON} "
                         "(PAN {PAN}), on {n:date}."),
    ("dc02", "document", "Medical certificate: This is to certify that {PERSON}, born {DATE_OF_BIRTH}, was under "
                         "treatment at {n:company} Hospital from {n:date}."),
    ("dc03", "document", "Nominee details - Name: {PERSON}; Relationship: {n:relation}; Contact: {PHONE}; "
                         "Aadhaar: {AADHAAR}"),
    ("dc04", "document", "Server handover notes: bastion {IP_ADDRESS}, root password {PASSWORD}, "
                         "GitHub token {API_KEY}. Rotate after {n:date}."),
    ("dc05", "document", "Reference check: {PERSON} can be contacted at {PHONE} / {EMAIL}. Previous employer: "
                         "{n:company}."),
    ("dc06", "document", "Wire instructions: credit {BANK_ACCOUNT} held by {PERSON}. Confirm by phone {PHONE} "
                         "before releasing {n:amount}."),

    # --- hard negatives: look sensitive, contain nothing labelled
    ("ng01", "neg_log", "{n:timestamp} INFO deploy: build {n:sha} rolled out to {n:env} (version {n:version}), "
                        "request_id={n:uuid}"),
    ("ng02", "neg_email", "Hi team, order {n:order_id} worth {n:amount} has shipped. Tracking AWB {n:awb}. "
                          "Invoice {n:invoice_no} attached.\nThanks, {n:company} Logistics"),
    ("ng03", "neg_form", "Card number: {n:masked_card}\nPassword: {n:redacted}\nAadhaar: XXXX XXXX {n:last4}"),
    ("ng04", "neg_code", "UUID_NAMESPACE = \"{n:uuid}\"\nCOMMIT = \"{n:short_sha}\"\nMAX_RETRIES = {n:small_int}\n"
                         "PORT = {n:port}"),
    ("ng05", "neg_doc", "{n:company} reported revenue of {n:amount} in {n:year}, up {n:percent} year on year, "
                        "according to filings from {n:city}."),
    ("ng06", "neg_ticket", "Ticket {n:ticket}: dashboard at {n:url} shows {n:int} errors since {n:date}. "
                           "Assigned to {n:department}."),
    ("ng07", "neg_json", "{{\"event\": \"page_view\", \"session\": \"{n:uuid}\", \"ts\": \"{n:timestamp}\", "
                         "\"latency_ms\": {n:int}}}"),
    ("ng08", "neg_chat", "reminder: standup at {n:time}, release {n:version} goes out {n:date}. "
                         "ping {n:department} for access"),
    ("ng09", "neg_env", "API_KEY=<your-api-key-here>\nPASSWORD=changeme\nDB_HOST=localhost\nTIMEOUT={n:small_int}"),
    ("ng10", "neg_invoice", "Purchase order {n:order_id} dated {n:date}. Qty {n:small_int} units of {n:product}. "
                            "HSN {n:hsn}. Total {n:amount}."),
]


# ----------------------------------------------------------------------------- checksums

_V_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
        [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
        [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
        [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_V_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
        [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 7, 6, 0, 8], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
        [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]
_V_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_check_digit(digits):
    c = 0
    for i, d in enumerate(reversed(digits)):
        c = _V_D[c][_V_P[(i + 1) % 8][int(d)]]
    return str(_V_INV[c])


def verhoeff_valid(digits):
    c = 0
    for i, d in enumerate(reversed(digits)):
        c = _V_D[c][_V_P[i % 8][int(d)]]
    return c == 0


def luhn_check_digit(digits):
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return str((10 - total % 10) % 10)


def luhn_valid(digits):
    return luhn_check_digit(digits[:-1]) == digits[-1]


# ----------------------------------------------------------------------------- value generators

class Values:
    def __init__(self, seed):
        self.r = random.Random(seed)
        self.fk = {"in": Faker("en_IN"), "us": Faker("en_US")}
        for i, f in enumerate(self.fk.values()):
            f.seed_instance(seed + i)

    def faker(self):
        return self.fk["in"] if self.r.random() < 0.7 else self.fk["us"]

    def digits(self, n):
        return "".join(self.r.choice(string.digits) for _ in range(n))

    def group(self, s, size, sep):
        return sep.join(s[i:i + size] for i in range(0, len(s), size))

    # sensitive ---------------------------------------------------------------
    def PERSON(self):
        f = self.faker()
        roll = self.r.random()
        if roll < 0.6:
            return f.name()
        if roll < 0.8:
            return f"{f.first_name()} {f.last_name()}"
        if roll < 0.9:
            return f.first_name()
        return f"{f.prefix()} {f.last_name()}".strip()

    def EMAIL(self):
        f = self.faker()
        if self.r.random() < 0.5:
            return f.email()
        first, last = f.first_name().lower(), f.last_name().lower()
        user = self.r.choice([f"{first}.{last}", f"{first}{self.digits(2)}", f"{first[0]}{last}", f"{first}_{last}"])
        return f"{user}@{self.r.choice(['gmail.com', 'yahoo.co.in', 'outlook.com', 'rediffmail.com', f.domain_name()])}"

    def PHONE(self):
        if self.r.random() < 0.7:
            num = self.r.choice("6789") + self.digits(9)
            return self.r.choice([
                f"+91 {num}", f"+91-{num}", f"{num}", f"{num[:5]} {num[5:]}", f"+91 {num[:5]} {num[5:]}",
                f"0{num}", f"(+91) {num[:3]}-{num[3:6]}-{num[6:]}",
            ])
        return self.fk["us"].phone_number()

    def ADDRESS(self):
        f = self.faker()
        addr = f.address().replace("\n", ", ")
        return addr

    def DATE_OF_BIRTH(self):
        d = date(1950, 1, 1) + timedelta(days=self.r.randint(0, 365 * 55))
        return self.r.choice([
            d.strftime("%d/%m/%Y"), d.strftime("%d-%m-%Y"), d.strftime("%Y-%m-%d"),
            d.strftime("%d %B %Y"), d.strftime("%b %d, %Y"), d.strftime("%d.%m.%Y"),
        ])

    def AADHAAR(self):
        base = self.r.choice("23456789") + self.digits(10)
        num = base + verhoeff_check_digit(base)
        return self.r.choice([self.group(num, 4, " "), num, self.group(num, 4, "-")])

    def PAN(self):
        letters = string.ascii_uppercase
        return ("".join(self.r.choice(letters) for _ in range(3)) + self.r.choice("PPPPCHFAT")
                + self.r.choice(letters) + self.digits(4) + self.r.choice(letters))

    def CREDIT_CARD(self):
        kind = self.r.choice(["visa", "visa", "master", "rupay", "amex"])
        if kind == "amex":
            base = self.r.choice(["34", "37"]) + self.digits(12)
            num = base + luhn_check_digit(base)
            return self.r.choice([num, f"{num[:4]} {num[4:10]} {num[10:]}", f"{num[:4]}-{num[4:10]}-{num[10:]}"])
        prefix = {"visa": "4", "master": self.r.choice(["51", "52", "53", "54", "55"]),
                  "rupay": self.r.choice(["60", "65", "81", "82"])}[kind]
        base = prefix + self.digits(15 - len(prefix))
        num = base + luhn_check_digit(base)
        return self.r.choice([num, self.group(num, 4, " "), self.group(num, 4, "-")])

    def BANK_ACCOUNT(self):
        return self.digits(self.r.choice([9, 10, 11, 12, 14, 15, 16, 18]))

    def IP_ADDRESS(self):
        f = self.fk["us"]
        roll = self.r.random()
        if roll < 0.6:
            return f.ipv4_public()
        if roll < 0.85:
            return f.ipv4_private()
        return f.ipv6()

    def API_KEY(self):
        alnum = string.ascii_letters + string.digits
        rnd = lambda n, chars=alnum: "".join(self.r.choice(chars) for _ in range(n))  # noqa: E731
        return self.r.choice([
            lambda: "sk-" + rnd(48),
            lambda: "sk-proj-" + rnd(40) + "_" + rnd(20),
            lambda: "AKIA" + rnd(16, string.ascii_uppercase + string.digits),
            lambda: "ghp_" + rnd(36),
            lambda: "AIza" + rnd(35, alnum + "-_"),
            lambda: "xoxb-" + self.digits(11) + "-" + self.digits(12) + "-" + rnd(24),
            lambda: "sk_live_" + rnd(24),
            lambda: "rzp_live_" + rnd(14),
        ])()

    def PASSWORD(self):
        f = self.fk["us"]
        roll = self.r.random()
        if roll < 0.4:
            return f.password(length=self.r.randint(8, 16))
        if roll < 0.7:
            word = self.r.choice(["Summer", "Welcome", "India", "Admin", "Monkey", "Dragon", "Cricket", "Mumbai"])
            return f"{word}{self.r.choice(['@', '#', '!', '$', ''])}{self.r.randint(1, 2026)}"
        return "".join(self.r.choice(string.ascii_letters + string.digits + "!@#$%^&*") for _ in range(12))

    # fillers (not labelled) --------------------------------------------------
    def filler(self, name):
        f = self.fk["us"]
        r = self.r
        rnd_date = date(2022, 1, 1) + timedelta(days=r.randint(0, 1400))
        table = {
            "month": lambda: rnd_date.strftime("%B %Y"),
            "company": lambda: self.faker().company(),
            "customer_ref": lambda: f"CUST{self.digits(6)}",
            "order_id": lambda: r.choice([f"ORD-{rnd_date.year}-{self.digits(6)}", f"#{self.digits(8)}",
                                          f"OD{self.digits(15)}"]),
            "time": lambda: f"{r.randint(0, 23):02d}:{r.randint(0, 59):02d}",
            "ifsc": lambda: r.choice(["HDFC", "ICIC", "SBIN", "UTIB", "KKBK"]) + "0" + self.digits(6),
            "city": lambda: self.faker().city(),
            "amount": lambda: r.choice([f"₹{r.randint(100, 999999):,}.00", f"Rs. {r.randint(100, 99999)}",
                                        f"${r.randint(5, 9999)}.{self.digits(2)}", f"INR {r.randint(1000, 500000)}"]),
            "date": lambda: r.choice([rnd_date.strftime("%d/%m/%Y"), rnd_date.strftime("%Y-%m-%d"),
                                      rnd_date.strftime("%d %b %Y")]),
            "department": lambda: r.choice(["Cardiology", "Finance", "IT Ops", "HR", "Platform", "Billing", "SRE"]),
            "expiry": lambda: f"{r.randint(1, 12):02d}/{r.randint(26, 32)}",
            "timestamp": lambda: f"{rnd_date.isoformat()}T{r.randint(0, 23):02d}:{r.randint(0, 59):02d}:"
                                 f"{r.randint(0, 59):02d}Z",
            "uuid": lambda: str(uuid.UUID(int=r.getrandbits(128), version=4)),
            "sha": lambda: "%040x" % r.getrandbits(160),
            "short_sha": lambda: "%07x" % r.getrandbits(28),
            "url": lambda: r.choice(["https://api.example.com/v1/charge", "https://status.internal/dash",
                                     f"https://{f.domain_name()}/{f.uri_path()}"]),
            "int": lambda: str(r.randint(1, 99999)),
            "small_int": lambda: str(r.randint(1, 60)),
            "env": lambda: r.choice(["prod", "staging", "dev", "qa"]),
            "version": lambda: f"{r.randint(0, 9)}.{r.randint(0, 30)}.{r.randint(0, 99)}",
            "port": lambda: str(r.choice([25, 465, 587, 3306, 5432, 6379, 8080, 8443])),
            "emp_code": lambda: f"EMP{self.digits(5)}",
            "invoice_no": lambda: r.choice([f"INV/{rnd_date.year % 100}-{rnd_date.year % 100 + 1}/{self.digits(4)}",
                                            f"INV-{self.digits(7)}"]),
            "ticket": lambda: r.choice([f"TCK-{self.digits(5)}", f"JIRA-{self.digits(4)}", f"#{self.digits(6)}"]),
            "relation": lambda: r.choice(["Spouse", "Son", "Daughter", "Mother", "Father", "Brother"]),
            "awb": lambda: self.digits(r.choice([10, 11, 12])),
            "masked_card": lambda: r.choice([f"XXXX-XXXX-XXXX-{self.digits(4)}", f"**** **** **** {self.digits(4)}"]),
            "redacted": lambda: r.choice(["********", "<REDACTED>", "[hidden]", "••••••••"]),
            "last4": lambda: self.digits(4),
            "year": lambda: str(r.randint(2015, 2026)),
            "percent": lambda: f"{r.randint(1, 60)}%",
            "product": lambda: r.choice(["LED panels", "steel bolts", "A4 paper reams", "router units", "chairs"]),
            "hsn": lambda: self.digits(r.choice([4, 6, 8])),
        }
        return table[name]()


# ----------------------------------------------------------------------------- rendering

def render(template, values, labels):
    out, spans, pos, last = [], [], 0, 0
    for m in PLACEHOLDER.finditer(template):
        lit = template[last:m.start()].replace("{{", "{").replace("}}", "}")
        out.append(lit)
        pos += len(lit)
        is_filler, name = m.group(1), m.group(2)
        if is_filler:
            val = values.filler(name)
        else:
            if name not in labels:
                raise ValueError(f"unknown label {name} in template")
            val = getattr(values, name)()
            spans.append({"start": pos, "end": pos + len(val), "label": name, "value": val})
        out.append(val)
        pos += len(val)
        last = m.end()
    out.append(template[last:].replace("{{", "{").replace("}}", "}"))
    return "".join(out), spans


def template_labels(text):
    return {m.group(2) for m in PLACEHOLDER.finditer(text) if not m.group(1)}


def split_templates(templates, labels, seed, fracs=(0.6, 0.2, 0.2)):
    """Assign whole templates to splits; every split must cover every label
    and contain at least one hard-negative template."""
    pos = [t for t in templates if not t[1].startswith("neg")]
    neg = [t for t in templates if t[1].startswith("neg")]
    for attempt in range(5000):
        r = random.Random(seed + attempt)
        p, n = pos[:], neg[:]
        r.shuffle(p)
        r.shuffle(n)
        cut = lambda xs: (int(len(xs) * fracs[0]), int(len(xs) * (fracs[0] + fracs[1])))  # noqa: E731
        a, b = cut(p)
        na, nb = max(1, int(len(n) * fracs[0])), max(2, int(len(n) * (fracs[0] + fracs[1])))
        splits = {"train": p[:a] + n[:na], "val": p[a:b] + n[na:nb], "test": p[b:] + n[nb:]}
        ok = all(
            set(labels) <= set().union(*(template_labels(t[2]) for t in ts))
            and any(t[1].startswith("neg") for t in ts)
            for ts in splits.values()
        )
        if ok:
            return splits
    raise RuntimeError("could not find a template split covering all labels; add more templates")


def make_rows(split, templates, n, values, labels, neg_frac, long_frac, version):
    pos = [t for t in templates if not t[1].startswith("neg")]
    neg = [t for t in templates if t[1].startswith("neg")]
    r = values.r

    def one():
        pool = neg if r.random() < neg_frac else pos
        t = r.choice(pool)
        text, spans = render(t[2], values, labels)
        return t[0], text, spans

    rows = []
    for i in range(n):
        if r.random() < long_frac:
            # long multi-part document: exercises the 512-token sliding window
            parts, spans, tids, offset = [], [], [], 0
            for _ in range(r.randint(12, 30)):
                tid, text, sp = one()
                parts.append(text)
                spans += [{**s, "start": s["start"] + offset, "end": s["end"] + offset} for s in sp]
                tids.append(tid)
                offset += len(text) + 2
            text = "\n\n".join(parts)
            rows.append({"id": f"{version}-{split}-{i:05d}", "text": text, "spans": spans,
                         "template_id": "+".join(sorted(set(tids))), "long": True})
        else:
            tid, text, spans = one()
            rows.append({"id": f"{version}-{split}-{i:05d}", "text": text, "spans": spans, "template_id": tid})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/v0")
    ap.add_argument("--labels", default=str(Path(__file__).resolve().parent.parent / "configs" / "labels.yaml"))
    ap.add_argument("--train", type=int, default=6000)
    ap.add_argument("--val", type=int, default=600)
    ap.add_argument("--test", type=int, default=600)
    ap.add_argument("--neg-frac", type=float, default=0.2)
    ap.add_argument("--long-frac", type=float, default=0.03)
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()

    labels = load_labels(args.labels)
    out = Path(args.out)
    version = out.name
    splits = split_templates(TEMPLATES, labels, args.seed)

    manifest = {"version": version, "generator_version": GENERATOR_VERSION, "seed": args.seed,
                "labels": labels, "splits": {}}
    sizes = {"train": args.train, "val": args.val, "test": args.test}
    for i, (split, templates) in enumerate(splits.items()):
        values = Values(args.seed * 100 + i)
        rows = make_rows(split, templates, sizes[split], values, labels, args.neg_frac, args.long_frac, version)
        path = out / f"{split}.jsonl"
        write_jsonl(path, rows)
        counts = Counter(s["label"] for r in rows for s in r["spans"])
        manifest["splits"][split] = {
            "rows": len(rows),
            "rows_without_spans": sum(1 for r in rows if not r["spans"]),
            "templates": sorted(t[0] for t in templates),
            "span_counts": dict(sorted(counts.items())),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        print(f"{split}: {len(rows)} rows, {sum(counts.values())} spans, templates={len(templates)}")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {out}/manifest.json")


if __name__ == "__main__":
    main()
