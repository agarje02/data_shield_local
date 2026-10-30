"""Value generators.

SensitiveValues  -> one method per label in configs/labels.yaml (these become spans)
LookalikeValues  -> harmless values that share a format with a sensitive one
                    (these are never labelled; they teach the model what NOT to flag)
Filler           -> neutral words and numbers for prose
"""
import base64
import random
import string
import uuid
from datetime import date, timedelta

from faker import Faker

from checksums import (iban_check_digits, luhn_check_digit, luhn_valid, verhoeff_check_digit,  # noqa: F401
                       verhoeff_valid)

# ----------------------------------------------------------------------------- shared helpers

class _Base:
    def __init__(self, seed):
        self.r = random.Random(seed)
        self.fk_in = Faker("en_IN")
        self.fk_us = Faker("en_US")
        self.fk_in.seed_instance(seed)
        self.fk_us.seed_instance(seed + 1)

    def faker(self):
        return self.fk_in if self.r.random() < 0.7 else self.fk_us

    def digits(self, n, first_nonzero=False):
        s = "".join(self.r.choice(string.digits) for _ in range(n))
        if first_nonzero and s[0] == "0":
            s = self.r.choice("123456789") + s[1:]
        return s

    def alnum(self, n, chars=string.ascii_letters + string.digits):
        return "".join(self.r.choice(chars) for _ in range(n))

    def group(self, s, size, sep):
        return sep.join(s[i:i + size] for i in range(0, len(s), size))

    def a_date(self, start=date(2021, 1, 1), days=1800):
        return start + timedelta(days=self.r.randint(0, days))

    def handle(self, name):
        """A name as it appears in a login / UPI handle: lowercase ASCII letters and digits only."""
        return "".join(c for c in name.lower() if c.isascii() and c.isalnum()) or "user"

    def b64(self, n_bytes, urlsafe=False):
        raw = bytes(self.r.getrandbits(8) for _ in range(n_bytes))
        return (base64.urlsafe_b64encode(raw).rstrip(b"=") if urlsafe else base64.b64encode(raw)).decode()

    def pem(self, kind, n_bytes):
        body = self.b64(n_bytes)
        lines = "\n".join(body[i:i + 64] for i in range(0, len(body), 64))
        return f"-----BEGIN {kind}-----\n{lines}\n-----END {kind}-----"

    def mm_yy(self):
        m, y = self.r.randint(1, 12), self.r.randint(2024, 2033)
        return self.r.choice([f"{m:02d}/{y % 100:02d}", f"{m:02d}/{y}", f"{m:02d}-{y % 100:02d}"])

    def fmt_date(self, d):
        return self.r.choice([d.strftime("%d/%m/%Y"), d.strftime("%d-%m-%Y"), d.strftime("%Y-%m-%d"),
                              d.strftime("%d %B %Y"), d.strftime("%b %d, %Y"), d.strftime("%d.%m.%Y")])


# ----------------------------------------------------------------------------- sensitive values

class SensitiveValues(_Base):
    prefixless_keys = False  # data v2.2+: some API keys without a provider prefix

    def PERSON(self):
        f = self.faker()
        roll = self.r.random()
        if roll < 0.06:
            return f"{f.first_name()} {f.last_name()}".lower()  # chat style
        if roll < 0.55:
            return f.name()
        if roll < 0.75:
            return f"{f.first_name()} {f.last_name()}"
        if roll < 0.88:
            return f.first_name()
        if roll < 0.94:
            return f"{f.first_name()[0]}. {f.last_name()}"
        return f"{self.r.choice(['Mr.', 'Ms.', 'Mrs.', 'Dr.'])} {f.last_name()}"

    def EMAIL(self):
        f = self.faker()
        if self.r.random() < 0.4:
            return f.email()
        first, last = f.first_name().lower(), f.last_name().lower()
        user = self.r.choice([f"{first}.{last}", f"{first}{self.digits(2)}", f"{first[0]}{last}",
                              f"{first}_{last}", f"{last}.{first}{self.digits(1)}"])
        domain = self.r.choice(["gmail.com", "yahoo.co.in", "outlook.com", "rediffmail.com", "hotmail.com",
                                "icloud.com", "protonmail.com", f.domain_name()])
        return f"{user}@{domain}"

    def PHONE(self):
        if self.r.random() < 0.75:
            n = self.r.choice("6789") + self.digits(9)
            return self.r.choice([
                f"+91 {n}", f"+91-{n}", n, f"{n[:5]} {n[5:]}", f"+91 {n[:5]} {n[5:]}", f"0{n}",
                f"(+91) {n[:3]}-{n[3:6]}-{n[6:]}", f"+91{n}", f"91-{n}", f"{n[:3]}-{n[3:6]}-{n[6:]}",
            ])
        return self.fk_us.phone_number()

    def ADDRESS(self):
        f = self.faker()
        if f is self.fk_in and self.r.random() < 0.5:
            return (f"{self.r.randint(1, 999)}, {f.street_name()}, {f.city()}, "
                    f"{f.state()} {self.digits(6, first_nonzero=True)}")
        return f.address().replace("\n", ", ")

    def DATE_OF_BIRTH(self):
        return self.fmt_date(date(1950, 1, 1) + timedelta(days=self.r.randint(0, 365 * 55)))

    def AADHAAR(self):
        base = self.r.choice("23456789") + self.digits(10)
        num = base + verhoeff_check_digit(base)
        return self.r.choice([self.group(num, 4, " "), num, self.group(num, 4, "-")])

    def PAN(self):
        up = string.ascii_uppercase
        return (self.alnum(3, up) + self.r.choice("PPPPPCHFAT") + self.r.choice(up)
                + self.digits(4) + self.r.choice(up))

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
        if self.r.random() < 0.15:  # international account: IBAN
            country, bban = self.r.choice([
                ("GB", self.alnum(4, string.ascii_uppercase) + self.digits(14)),
                ("DE", self.digits(18)), ("AE", self.digits(19)), ("FR", self.digits(23)),
                ("NL", self.alnum(4, string.ascii_uppercase) + self.digits(10))])
            iban = country + iban_check_digits(country, bban) + bban
            return self.r.choice([iban, self.group(iban, 4, " ")])
        return self.digits(self.r.choice([9, 10, 11, 12, 13, 14, 15, 16, 18]), first_nonzero=self.r.random() < 0.7)

    def IP_ADDRESS(self):
        roll = self.r.random()
        if roll < 0.55:
            return self.fk_us.ipv4_public()
        if roll < 0.85:
            return self.fk_us.ipv4_private()
        return self.fk_us.ipv6()

    def API_KEY(self):
        a = string.ascii_letters + string.digits
        if self.prefixless_keys and self.r.random() < 0.2:
            # some providers issue plain random keys; only the context ("api key", "secret") says so
            return self.alnum(self.r.choice([32, 40, 48]))
        return self.r.choice([
            lambda: "sk-" + self.alnum(48),
            lambda: "sk-proj-" + self.alnum(40) + "_" + self.alnum(20),
            lambda: "sk-ant-api03-" + self.alnum(80, a + "-_"),
            lambda: "AKIA" + self.alnum(16, string.ascii_uppercase + string.digits),
            lambda: "ghp_" + self.alnum(36),
            lambda: "github_pat_" + self.alnum(22) + "_" + self.alnum(59),
            lambda: "AIza" + self.alnum(35, a + "-_"),
            lambda: "xoxb-" + self.digits(11) + "-" + self.digits(12) + "-" + self.alnum(24),
            lambda: "sk_live_" + self.alnum(24),
            lambda: "rzp_live_" + self.alnum(14),
            lambda: "hf_" + self.alnum(34),
            lambda: "glpat-" + self.alnum(20, a + "-_"),
        ])()

    def PASSWORD(self):
        roll = self.r.random()
        sym = self.r.choice("@#$&!_%*")
        word = self.fk_us.word()
        if roll < 0.25:
            return self.fk_us.password(length=self.r.randint(8, 16))
        if roll < 0.38:  # word + digits + symbol + capital: hello42_K
            return f"{word}{self.digits(2)}{sym}{self.r.choice(string.ascii_uppercase)}"
        if roll < 0.48:  # Word&2024, Word@1990#Tail
            return f"{word.capitalize()}{sym}{self.r.randint(1980, 2026)}" + self.r.choice(["", "#" + word[:4].capitalize()])
        if roll < 0.55:  # passphrase
            return "-".join(self.fk_us.words(self.r.randint(3, 4))) + str(self.r.randint(1, 99))
        if roll < 0.75:
            word = self.r.choice(["Summer", "Welcome", "India", "Admin", "Monkey", "Dragon", "Cricket",
                                  "Mumbai", "Sunshine", "Qwerty", "Ganesh", "Krishna", "Football"])
            return f"{word}{self.r.choice(['@', '#', '!', '$', '', '_'])}{self.r.randint(1, 2026)}"
        return self.alnum(self.r.randint(10, 16), string.ascii_letters + string.digits + "!@#$%^&*")

    # -- financial
    UPI_HANDLES = ["okhdfcbank", "okicici", "oksbi", "okaxis", "ybl", "ibl", "axl", "paytm", "apl", "upi",
                   "kotak", "icici", "sbi", "hdfcbank", "axisbank", "freecharge", "jupiteraxis", "slc"]

    def UPI_ID(self):
        f = self.faker()
        first, last = self.handle(f.first_name()), self.handle(f.last_name())
        user = self.r.choice([f"{first}.{last}", f"{first}{self.digits(2)}", f"{first}{last[0]}",
                              self.r.choice("6789") + self.digits(9), f"{first}.{last}{self.digits(3)}"])
        return f"{user}@{self.r.choice(self.UPI_HANDLES)}"

    def CVV(self):
        return self.digits(4 if self.r.random() < 0.1 else 3)

    def CARD_EXPIRY(self):
        return self.mm_yy()

    def OTP_PIN(self):
        return self.digits(self.r.choice([4, 4, 6, 6, 6, 8]))

    # -- credentials
    def USERNAME(self):
        f = self.faker()
        first, last = self.handle(f.first_name()), self.handle(f.last_name())
        return self.r.choice([f.user_name(), f"{first}.{last}{self.digits(2)}", f"{first[0]}{last}",
                              f"{first}_{last[:3]}", f"{first}{self.r.randint(1980, 2005)}", f"{last}.{first[0]}"])

    def AUTH_TOKEN(self):
        r = self.r
        roll = r.random()
        if roll < 0.5:  # JWT
            header = r.choice(['{"alg":"HS256","typ":"JWT"}', '{"alg":"RS256","typ":"JWT"}', '{"typ":"JWT","alg":"ES256"}'])
            claims = '{"sub":"%d","iat":%d,"exp":%d}' % (r.randint(1000, 99999999), r.randint(1600000000, 1800000000),
                                                         r.randint(1800000000, 1900000000))
            enc = [base64.urlsafe_b64encode(x.encode()).rstrip(b"=").decode() for x in (header, claims)]
            return f"{enc[0]}.{enc[1]}.{self.b64(32, urlsafe=True)}"
        if roll < 0.65:  # Google OAuth access token
            return "ya29." + self.b64(r.randint(60, 120), urlsafe=True)
        if roll < 0.8:
            return self.b64(r.randint(24, 48), urlsafe=True)
        return self.alnum(r.choice([32, 40, 64]))

    def PRIVATE_KEY(self):
        kind, n = self.r.choice([("EC PRIVATE KEY", 121), ("PRIVATE KEY", 48), ("OPENSSH PRIVATE KEY", 260),
                                 ("EC PRIVATE KEY", 121), ("RSA PRIVATE KEY", self.r.choice([608, 1192]))])
        return self.pem(kind, n)

    def CONNECTION_STRING(self):
        f = self.fk_us
        user = self.r.choice([f.user_name(), "app", "admin", "svc_api", "readonly", "etl_user"])
        pw = self.PASSWORD()
        while any(w in pw.lower() for w in ("example", "replace", "your_")):  # read as placeholders by validators
            pw = self.PASSWORD()
        pw = pw.replace("%", "%25").replace("@", "%40").replace("/", "%2F").replace(":", "%3A")
        host = self.r.choice([f.domain_word() + ".internal", f"db-{self.digits(2)}.{f.domain_name()}",
                              f.ipv4_private(), f"{f.domain_word()}.rds.amazonaws.com"])
        db = f.word()
        return self.r.choice([
            f"postgres://{user}:{pw}@{host}:5432/{db}", f"postgresql://{user}:{pw}@{host}/{db}",
            f"mysql://{user}:{pw}@{host}:3306/{db}", f"mongodb+srv://{user}:{pw}@{f.domain_word()}.mongodb.net/{db}",
            f"redis://:{pw}@{host}:6379/0", f"amqp://{user}:{pw}@{host}:5672/",
            f"Server={host};Database={db};User Id={user};Password={pw};",
        ])


# ----------------------------------------------------------------------------- harmless look-alikes

class LookalikeValues(_Base):
    """Each method returns a value that LOOKS like a sensitive type but is not.
    The comment names the sensitive type it imitates."""

    # look like PHONE / BANK_ACCOUNT
    def awb(self):
        return self.digits(self.r.choice([10, 11, 12]), first_nonzero=True)

    def order_no(self):
        return self.r.choice([self.digits(10, True), f"OD{self.digits(15)}", f"#{self.digits(8)}",
                              f"ORD-{self.r.randint(2021, 2026)}-{self.digits(6)}"])

    def toll_free(self):
        return self.r.choice([f"1800-{self.digits(3)}-{self.digits(4)}", f"1800 {self.digits(3)} {self.digits(4)}",
                              f"1860-{self.digits(3)}-{self.digits(4)}"])

    def employee_id(self):
        return self.r.choice([self.digits(self.r.choice([6, 8, 9]), True), f"EMP{self.digits(5)}"])

    def policy_no(self):
        return self.digits(self.r.choice([9, 10, 12, 14]), True)

    def ticket_no(self):
        return self.r.choice([self.digits(9, True), f"TCK-{self.digits(6)}", f"INC{self.digits(7)}"])

    def epoch_ms(self):
        return str(self.r.randint(1600000000000, 1800000000000))

    # look like AADHAAR (12 digits)
    def upi_ref(self):
        return self.digits(12, True)

    def rrn(self):
        return self.digits(12, True)

    def invoice_serial(self):
        return self.r.choice([self.group(self.digits(12, True), 4, " "), self.group(self.digits(12, True), 4, "-")])

    # look like PAN (AAAAA9999A)
    def sku(self):
        up = string.ascii_uppercase
        return self.alnum(5, up) + self.digits(4) + self.r.choice(up)

    def gstin(self):
        up = string.ascii_uppercase
        return f"{self.r.randint(1, 37):02d}" + self.alnum(5, up) + self.digits(4) + self.r.choice(up) \
            + self.r.choice("123456789") + "Z" + self.r.choice(up + string.digits)

    def pnr(self):
        return self.alnum(6, string.ascii_uppercase + string.digits)

    def coupon(self):
        return self.r.choice(["SAVE", "FEST", "NEW", "FLAT"]) + str(self.r.randint(10, 75)) + self.alnum(4, string.ascii_uppercase)

    # look like CREDIT_CARD
    # look like CVV (3-4 digits)
    def http_code(self):
        return str(self.r.choice([200, 201, 301, 302, 400, 401, 403, 404, 409, 429, 500, 502, 503, 504]))

    def room_no(self):
        return str(self.r.randint(101, 999))

    # look like CARD_EXPIRY (MM/YY)
    def offer_validity(self):
        return self.mm_yy()

    # look like OTP_PIN (4-8 digits)
    def year(self):
        return str(self.r.randint(1990, 2030))

    def extension(self):
        return self.digits(4, True)

    def masked_card(self):
        return self.r.choice([f"XXXX-XXXX-XXXX-{self.digits(4)}", f"**** **** **** {self.digits(4)}",
                              f"ending {self.digits(4)}"])

    def gift_card(self):
        return self.group(self.alnum(16, string.ascii_uppercase + string.digits), 4, "-")

    # look like API_KEY / PASSWORD
    def git_sha(self):
        return self.r.choice(["%040x" % self.r.getrandbits(160), "%07x" % self.r.getrandbits(28)])

    def uuid(self):
        return str(uuid.UUID(int=self.r.getrandbits(128), version=4))

    def sha256(self):
        return "%064x" % self.r.getrandbits(256)

    def integrity_hash(self):
        return "sha512-" + self.alnum(86, string.ascii_letters + string.digits + "+/") + "=="

    def example_key(self):
        return self.r.choice(["YOUR_API_KEY", "<your-api-key>", "sk-xxxxxxxxxxxxxxxxxxxx", "${OPENAI_API_KEY}",
                              "os.environ['API_KEY']", "REPLACE_ME", "<token>", "AKIAXXXXXXXXXXXXXXXX"])

    def placeholder_token(self):
        return self.r.choice(["<token>", "${ACCESS_TOKEN}", "xxxxxxxxxxxxxxxxxxxx", "<your-jwt>", "$TOKEN",
                              "{{auth_token}}", "REDACTED", "<access_token>", "eyJ..."])

    # look like USERNAME: shared system accounts identify no person
    def system_user(self):
        return self.r.choice(["admin", "root", "ubuntu", "postgres", "ec2-user", "svc-billing", "deploy-bot",
                              "jenkins", "www-data", "guest", "test", "nginx", "ci-runner", "sa"])

    # look like PRIVATE_KEY: public material is meant to be shared
    def public_key(self):
        return self.pem("PUBLIC KEY", self.r.choice([91, 294]))

    def certificate(self):
        return self.pem("CERTIFICATE", self.r.choice([420, 640]))

    def ssh_pubkey(self):
        return (f"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI{self.b64(32)[:43]} "
                f"{self.fk_us.user_name()}@{self.fk_us.domain_word()}")

    # look like CONNECTION_STRING: no credentials inside
    def conn_no_creds(self):
        host = self.r.choice(["localhost", "db.internal", "cache", "10.0.0.12", "mq.svc.cluster.local"])
        db = self.fk_us.word()
        return self.r.choice([f"postgres://{host}:5432/{db}", f"redis://{host}:6379/0", f"mysql://{host}/{db}",
                              f"mongodb://{host}:27017/{db}", f"postgres://${{DB_USER}}:${{DB_PASSWORD}}@{host}/{db}",
                              f"amqp://{host}:5672/"])

    def placeholder_password(self):
        return self.r.choice(["********", "<REDACTED>", "[hidden]", "changeme", "${DB_PASSWORD}", "<password>",
                              "xxxxxxxx", "N/A"])

    def config_value(self):
        return self.r.choice(["debug", "info", "true", "false", "utf-8", "us-east-1", "ap-south-1", "json",
                              "round_robin", "gzip", "postgres", "redis", "30s", "prod", "staging"])

    # look like IP_ADDRESS
    def version(self):
        return self.r.choice([f"{self.r.randint(0, 12)}.{self.r.randint(0, 30)}.{self.r.randint(0, 99)}",
                              f"{self.r.randint(1, 10)}.{self.r.randint(0, 9)}.{self.r.randint(0, 9)}.{self.r.randint(0, 999)}",
                              f"v{self.r.randint(0, 5)}.{self.r.randint(0, 20)}.{self.r.randint(0, 9)}"])

    def port(self):
        return str(self.r.choice([22, 25, 80, 443, 465, 587, 3000, 3306, 5432, 5672, 6379, 8000, 8080, 8443,
                                  9092, 9200, 27017]))

    def localhost(self):
        return self.r.choice(["localhost", "127.0.0.1", "0.0.0.0", "::1"])

    # look like DATE_OF_BIRTH
    def event_date(self):
        return self.fmt_date(self.a_date())

    # look like ADDRESS / PERSON
    def company(self):
        return self.faker().company()

    def city(self):
        return self.faker().city()

    def office_address(self):
        """A business address is public, not personal: e.g. a company's registered office."""
        f = self.faker()
        return f"{f.company()}, {f.street_name()}, {f.city()}"

    def product(self):
        return self.r.choice(["LED panels", "steel bolts", "A4 paper reams", "router units", "office chairs",
                              "laptop stands", "HDMI cables", "solar inverters", "PVC pipes", "safety helmets"])

    # look like generic numbers in PII
    def pincode(self):
        return self.digits(6, True)

    def ifsc(self):
        return self.r.choice(["HDFC", "ICIC", "SBIN", "UTIB", "KKBK", "PUNB", "BARB"]) + "0" + self.digits(6)

    def hsn(self):
        return self.digits(self.r.choice([4, 6, 8]), True)

    def amount(self):
        return self.r.choice([f"₹{self.r.randint(100, 999999):,}.00", f"Rs. {self.r.randint(100, 99999)}",
                              f"${self.r.randint(5, 9999)}.{self.digits(2)}", f"INR {self.r.randint(1000, 500000)}",
                              f"{self.r.randint(1, 99)}.{self.digits(1)} lakh"])

    def quantity(self):
        return str(self.r.randint(1, 5000))


class Filler(_Base):
    """Neutral fillers for prose frames (never labelled)."""

    def get(self, name):
        r = self.r
        lk = self._lk
        table = {
            "company": lk.company, "city": lk.city, "product": lk.product, "amount": lk.amount,
            "qty": lk.quantity, "date": lk.event_date, "version": lk.version,
            "int": lambda: str(r.randint(2, 480)),
            "percent": lambda: f"{r.randint(1, 60)}%",
            "quarter": lambda: f"Q{r.randint(1, 4)}",
            "month": lambda: self.a_date().strftime("%B"),
            "weekday": lambda: r.choice(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]),
            "time": lambda: f"{r.randint(8, 19):02d}:{r.choice(['00', '15', '30', '45'])}",
            "team": lambda: r.choice(["Finance", "Platform", "Support", "Sales", "HR", "SRE", "Design", "Legal"]),
            "service": lambda: r.choice(["auth-service", "payments", "search-api", "notifier", "kyc-worker",
                                         "billing", "gateway", "ingest"]),
            "env": lambda: r.choice(["prod", "staging", "dev", "qa"]),
            "level": lambda: r.choice(["INFO", "WARN", "DEBUG", "ERROR"]),
            "ms": lambda: str(r.randint(3, 2400)),
        }
        return table[name]()

    def __init__(self, seed, lookalikes):
        super().__init__(seed)
        self._lk = lookalikes
