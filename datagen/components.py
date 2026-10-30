"""Building blocks for documents. Everything here is DATA; compose.py assembles it.

{v} marks where a value goes. Every component has a stable id, and whole components
are assigned to train / val / test, so test wording is never seen in training.

POS_CUES      label -> phrases that introduce a sensitive value      ("call me on {v}")
NEG_CUES      look-alike method -> phrases for a harmless twin       ("AWB {v}")
              Several NEG cues reuse POS wording on purpose (contrast): "card number {v}" with a
              masked card, "password: {v}" with a placeholder, "API key {v}" with YOUR_API_KEY.
FRAMES        sentence wrappers around a cue phrase                  ("Please note {X}.")
NEUTRAL       clean sentences with {name} fillers from values.Filler
POS_KEYS      label -> field names for structured records (JSON, CSV, key=value)
NEG_KEYS      field name -> look-alike method
COMBOS        sentences with several {LABEL} slots, because real leaks cluster
              (card + expiry + CVV, username + password)

Label policies (v2):
  API_KEY            provider-issued static keys (sk-, AKIA, ghp_, ...)
  AUTH_TOKEN         JWT, OAuth bearer/refresh tokens, session cookie values
  USERNAME           a person's login handle; shared system accounts (root, admin, ubuntu) are not
  CONNECTION_STRING  only when it carries credentials; one span, no nested PASSWORD
  OTP_PIN            OTP, UPI PIN, ATM PIN, MPIN; a postal "PIN code" is not
  IBAN               labelled BANK_ACCOUNT; IFSC, GSTIN, SWIFT are business codes, not labelled
"""

POS_CUES = {
    "PERSON": ["{v} called this morning", "my name is {v}", "assigned to {v}", "{v} will join the call",
               "signed by {v}", "customer {v} reported the issue", "spoke to {v} yesterday", "patient {v}",
               "candidate {v}", "meeting with {v}", "{v} approved the request", "nominee is {v}",
               "account holder {v}", "{v} from accounts", "handed over to {v}"],
    "EMAIL": ["write to {v}", "email {v}", "send it to {v}", "my mail id is {v}", "reach me at {v}",
              "login {v}", "cc {v} on the thread", "registered email {v}", "from {v}", "user={v}",
              "contact {v} for access", "invite {v} to the drive", "alternate email {v}"],
    "PHONE": ["call me on {v}", "mob {v}", "Ph. {v}", "WhatsApp {v}", "contact number {v}", "my number is {v}",
              "Tel {v}", "alternate no. {v}", "mera number {v} hai", "OTP sent to {v}", "cell {v}",
              "ring me at {v}", "registered mobile {v}", "phone no {v}"],
    "ADDRESS": ["I live at {v}", "address {v}", "deliver to {v}", "residing at {v}", "permanent address {v}",
                "ship to {v}", "new address is {v}", "home address {v}", "correspondence address {v}",
                "shifted to {v}", "billing address {v}", "current address {v}"],
    "DATE_OF_BIRTH": ["born on {v}", "DOB {v}", "date of birth {v}", "D.O.B. {v}", "birthday is {v}",
                      "born {v}", "dob={v}", "birth date {v}", "DOB as per Aadhaar {v}", "age proof shows {v}",
                      "his DOB is {v}", "her date of birth is {v}"],
    "AADHAAR": ["Aadhaar {v}", "aadhaar no. {v}", "Aadhaar number {v}", "my aadhar is {v}",
                "aadhaar card {v}", "UIDAI number {v}", "e-KYC with Aadhaar {v}", "aadhaar no hai {v}",
                "Aadhaar linked {v}", "Aadhaar ID {v}", "aadhar card no {v}"],
    "PAN": ["PAN {v}", "PAN no. {v}", "PAN card {v}", "my pan is {v}", "income tax PAN {v}", "PAN number {v}",
            "pan={v}", "TDS against PAN {v}", "individual PAN {v}", "permanent account number {v}",
            "PAN linked {v}", "pan card no {v}"],
    "CREDIT_CARD": ["card {v}", "card number {v}", "credit card {v}", "charged to {v}", "debit card no. {v}",
                    "cc={v}", "paid using {v}", "Visa {v}", "card no {v} expiring soon", "my card is {v}",
                    "card on file {v}", "billed to card {v}"],
    "BANK_ACCOUNT": ["A/C {v}", "account number {v}", "acc no. {v}", "credit to account {v}",
                     "savings account {v}", "beneficiary a/c {v}", "my account no is {v}", "account {v}",
                     "transfer to {v}", "a/c no. {v} with IFSC", "current account {v}", "acct {v}",
                     "bank account {v}"],
    "IP_ADDRESS": ["from IP {v}", "host {v}", "server {v}", "src={v}", "login from {v}", "client_ip={v}",
                   "connect to {v}", "bastion {v}", "remote address {v}", "ssh admin@{v}", "VPN gateway {v}",
                   "whitelist {v}", "dst={v}"],
    "API_KEY": ["API key {v}", "token {v}", "api_key=\"{v}\"",
                "OPENAI_API_KEY={v}", "secret key {v}", "X-Api-Key: {v}", "access key {v}", "use this key {v}",
                "GITHUB_TOKEN={v}", "key {v} is live", "HF token {v}", "STRIPE_SECRET={v}"],
    "PASSWORD": ["password {v}", "pwd: {v}", "pass={v}", "DB_PASSWORD={v}", "login password is {v}",
                 "the password is {v}", "temp password {v}", "wifi password {v}", "credentials admin / {v}",
                 "passcode {v}", "new password: {v}", "root password {v}", "wifi pass is {v}",
                 "PGPASSWORD={v}", "login with {v}", "router password {v}"],
    # -- financial
    "UPI_ID": ["UPI ID {v}", "pay to {v}", "GPay {v}", "send money on {v}", "VPA {v}", "PhonePe {v}",
               "my UPI is {v}", "UPI: {v}", "Paytm UPI {v}", "collect request from {v}", "refund to {v}",
               "upi id hai {v}", "scan or pay {v}"],
    "CVV": ["cvv: {v}", "CVV2 {v}", "the 3 digit code on the back is {v}",
            "card verification value {v}", "cvc {v}", "CVV number {v}", "cvv hai {v}", "CID {v}",
            "card code {v}", "cvv={v}", "CVV: {v}", "CVV-{v}", "the CVV is {v}", "cvv code {v}",
            "CVV/CVC {v}", "card security code {v}", "code on the back {v}", "cvv no. {v}",
            "back of the card says {v}", "cvv likha hai {v}", "enter CVV {v}", "cvv {v} hai", "CVC2 {v}",
            "the cvv number is {v}", "3-digit CVV {v}", "Amex CID {v}", "cvv:{v}"],
    "CARD_EXPIRY": ["expiry {v}", "exp {v}", "valid thru {v}", "expires {v}", "card expiry {v}",
                    "exp date {v}", "expiry date: {v}", "VALID THRU {v}", "card valid upto {v}", "MM/YY {v}",
                    "expiring {v}", "exp={v}"],
    "OTP_PIN": ["OTP is {v}", "your OTP {v}", "UPI PIN {v}", "ATM PIN {v}", "MPIN {v}", "verification code {v}",
                "{v} is your OTP for login", "one time password {v}", "debit card PIN {v}", "OTP hai {v}",
                "use code {v} to sign in", "transaction PIN {v}", "2FA code {v}", "net banking OTP {v}",
                "OTP: {v}", "otp={v}", "OTP code {v}", "the OTP is {v}", "enter OTP {v}",
                "OTP {v} valid for 10 minutes", "{v} is your verification code", "your one-time code is {v}",
                "login code {v}", "UPI PIN is {v}", "my ATM pin is {v}", "card PIN {v}", "set new PIN {v}",
                "ATM pin hai {v}", "do not share OTP {v}", "{v} is the OTP", "TPIN {v}", "IPIN {v}",
                "app lock PIN {v}", "authenticator code {v}"],
    # -- credentials
    "USERNAME": ["user: {v}", "login id {v}", "user name {v}", "sign in as {v}", "uname={v}",
                 "my login is {v}", "user id {v}", "account {v} is locked", "logged in as {v}", "handle {v}",
                 "ssh {v}@server", "portal username {v}", "username: {v}", "Username {v}", "user id: {v}",
                 "userid={v}", "login: {v}", "login name {v}", "LDAP user {v}", "my username is {v}",
                 "the account name is {v}", "AD account {v}", "github user {v}", "employee login {v}",
                 "SAP user {v}", "net banking user id {v}", "Windows login {v}", "VPN user {v}",
                 "reset password for {v}", "user={v}"],
    "AUTH_TOKEN": ["Authorization: Bearer {v}", "access_token={v}", "refresh token {v}", "Cookie: session={v}",
                   "JWT {v}", "id_token: {v}", "bearer {v}", "x-auth-token: {v}", "session cookie {v}",
                   "token=\"{v}\"", "the access token is {v}", "Set-Cookie: sid={v}", "OAuth token {v}"],
    "PRIVATE_KEY": ["here is the key:\n{v}", "private key:\n{v}", "id_rsa:\n{v}", "SSH key\n{v}",
                    "server.key\n{v}", "the deploy key is\n{v}", "TLS key below\n{v}", "key.pem contents:\n{v}",
                    "use this key\n{v}", "signing key:\n{v}", "pasting the key\n{v}"],
    "CONNECTION_STRING": ["DATABASE_URL={v}", "connect with {v}", "conn string {v}", "MONGO_URI={v}",
                          "REDIS_URL={v}", "the DSN is {v}", "jdbc url {v}", "use {v} for the replica",
                          "connection string: {v}", "SQLALCHEMY_DATABASE_URI={v}", "broker url {v}",
                          "db url {v}"],
}

COMBOS = [
    "card no {CREDIT_CARD}, expiry {CARD_EXPIRY}",
    "{CREDIT_CARD} {CARD_EXPIRY} {CVV}", "Card: {CREDIT_CARD} | Exp: {CARD_EXPIRY} | CVV: {CVV}",
    "username {USERNAME} password {PASSWORD}", "login {USERNAME} / {PASSWORD}", "user {USERNAME} pwd {PASSWORD}",
    "id {USERNAME} and password {PASSWORD}", "user={USERNAME} password={PASSWORD}",
    "username: {USERNAME}, password: {PASSWORD}", "login {USERNAME} pass {PASSWORD}", "ID: {USERNAME} PW: {PASSWORD}",
    "{USERNAME} / {PASSWORD}", "login is {USERNAME}:{PASSWORD}", "use {USERNAME}:{PASSWORD} for the portal",
    "OTP {OTP_PIN} for payment to {UPI_ID}", "pay {UPI_ID}, PIN {OTP_PIN}",
    "account {BANK_ACCOUNT}, UPI {UPI_ID}", "{PERSON}, UPI {UPI_ID}", "{PERSON} login {USERNAME}",
    "email {EMAIL} and password {PASSWORD}", "card {CREDIT_CARD} cvv {CVV}", "CVV {CVV} exp {CARD_EXPIRY}",
    "{CARD_EXPIRY} cvv {CVV}", "a/c {BANK_ACCOUNT}, card {CREDIT_CARD}",
]

# Key contrasts and core synonyms (UID = Aadhaar, CSC = CVV) every split must contain,
# added after the disjoint split. Without them a
# 3-way split can put the only "PIN code 411001" (postal, not secret) or the only
# "user:pass" pattern into test, and the model never learns it. Everything else stays disjoint.
SHARED = {
    "pos_cues": {"CVV": ["CVV {v}", "security code {v}", "CSC {v}"], "OTP_PIN": ["OTP {v}"],
                 "USERNAME": ["username {v}"], "AADHAAR": ["UID {v}"]},
    "neg_cues": {"pincode": ["PIN code {v}", "pincode {v}"], "http_code": ["HTTP {v}", "error {v}"]},
    "combos": ["creds: {USERNAME}:{PASSWORD}", "card {CREDIT_CARD} exp {CARD_EXPIRY} cvv {CVV}"],
}

NEG_CUES = {
    "awb": ["AWB {v}", "tracking no. {v}", "consignment {v}", "docket number {v}"],
    "order_no": ["order {v}", "order no. {v}", "order id {v}", "purchase order {v}"],
    "toll_free": ["call our helpline {v}", "customer care {v}", "toll-free {v}"],
    "employee_id": ["employee ID {v}", "emp code {v}", "staff no. {v}"],
    "policy_no": ["policy number {v}", "policy no. {v}", "claim number {v}"],
    "ticket_no": ["ticket {v}", "incident {v}", "case #{v}"],
    "epoch_ms": ["ts={v}", "created_at {v}", "timestamp {v}"],
    "upi_ref": ["UPI ref {v}", "UTR {v}", "transaction id {v}"],
    "rrn": ["RRN {v}", "reference no. {v}", "bank ref {v}"],
    "invoice_serial": ["invoice serial {v}", "batch {v}", "serial number {v}"],
    "sku": ["SKU {v}", "item code {v}", "model no. {v}", "part number {v}"],
    "gstin": ["GSTIN {v}", "GST no. {v}", "GST registration {v}"],
    "pnr": ["PNR {v}", "booking ref {v}", "confirmation code {v}"],
    "coupon": ["coupon {v}", "promo code {v}", "use code {v}"],
    "masked_card": ["card {v}", "card number {v}", "paid with card {v}"],
    "gift_card": ["gift card {v}", "voucher {v}", "redeem code {v}"],
    "git_sha": ["commit {v}", "build {v}", "revision {v}"],
    "uuid": ["request_id={v}", "correlation id {v}", "trace id {v}"],
    "sha256": ["checksum {v}", "sha256 {v}", "digest {v}"],
    "integrity_hash": ["integrity \"{v}\"", "lockfile hash {v}", "subresource integrity {v}"],
    "example_key": ["API key {v}", "token {v}", "OPENAI_API_KEY={v}", "set api_key={v}"],
    "placeholder_password": ["password {v}", "pwd: {v}", "DB_PASSWORD={v}", "new password: {v}"],
    "config_value": ["log_level={v}", "mode {v}", "region {v}", "compression={v}"],
    "version": ["version {v}", "release {v}", "upgraded to {v}", "image tag {v}"],
    "port": ["port {v}", "listening on :{v}", "PORT={v}", "exposed port {v}"],
    "localhost": ["host {v}", "bind {v}", "connect to {v}"],
    "event_date": ["invoice date {v}", "due on {v}", "meeting on {v}", "released on {v}", "valid till {v}"],
    "company": ["vendor {v}", "client {v}", "partner {v}"],
    "city": ["branch in {v}", "office at {v}", "shipped from {v}"],
    "office_address": ["registered office {v}", "head office {v}", "corporate address {v}"],
    "pincode": ["postal code {v}", "delivery pincode {v}", "PIN {v} area", "pin code - {v}", "PIN: {v}",
                "PIN Code: {v}", "area pincode {v}", "Pune - {v}", "pin code no. {v}", "zip {v}",
                "pincode hai {v}", "office PIN code {v}"],
    "http_code": ["status {v}", "returned {v}", "got a {v} from the API", "responded {v}"],
    "room_no": ["room {v}", "flight AI {v}", "bus no. {v}", "cabin {v}"],
    "offer_validity": ["offer valid till {v}", "coupon expires {v}", "Mfg/Exp {v}", "best before {v}",
                       "warranty till {v}", "exp {v} on the pack"],
    "year": ["since {v}", "in {v}", "FY {v}", "batch of {v}"],
    "extension": ["ext {v}", "extension {v}", "intercom {v}", "model {v}"],
    "placeholder_token": ["Authorization: Bearer {v}", "access_token={v}", "token=\"{v}\"", "JWT {v}"],
    "system_user": ["username {v}", "sign in as {v}", "user: {v}", "ssh {v}@server", "run as {v}"],
    "public_key": ["public key:\n{v}", "here is my public key:\n{v}", "pubkey\n{v}"],
    "certificate": ["certificate:\n{v}", "CA cert\n{v}", "server.crt\n{v}"],
    "ssh_pubkey": ["add {v} to authorized_keys", "my public key {v}", "id_ed25519.pub: {v}"],
    "conn_no_creds": ["DATABASE_URL={v}", "connect with {v}", "REDIS_URL={v}", "the DSN is {v}"],
    "ifsc": ["IFSC {v}", "IFSC code {v}", "branch code {v}"],
    "hsn": ["HSN {v}", "HSN code {v}", "tariff code {v}"],
    "amount": ["amount {v}", "total {v}", "balance {v}", "EMI of {v}"],
    "quantity": ["qty {v}", "{v} units", "count {v}"],
}

FRAMES = [
    "{X}.", "Please note {X}.", "For reference, {X}.", "As discussed, {X}.", "FYI {X}.", "Can you check {X}?",
    "Update: {X}.", "{X}, please confirm.", "Just to confirm, {X}.", "Noted {X} in the tracker.",
    "{X}. Let me know if anything changes.", "Kindly verify {X} before Friday.", "We have recorded {X}.",
    "Per the form, {X}.", "Reminder: {X}.", "{X} (see attached).", "Hi, {X}.", "Thanks, {X}.", "I think {X}.",
    "Also, {X}.", "Earlier you said {X}?", "OK so {X}.", "pls note {X}", "bhai {X}", "{X}, thanks!",
    "In the call, {X}.", "Logged: {X}.", "Status: {X}.", "The record shows {X}.", "Escalation: {X}.",
    "Attaching details, {X}.", "{X} as requested.", "From the CRM: {X}.", "Note to self: {X}",
    "Quick one, {X}?", "Following up, {X}.", "Copying the team: {X}.", "{X}; the rest is unchanged.",
    "Got it, {X}.", "Correction: {X}.", "Sharing for the audit, {X}.", "As per our records, {X}.",
]

NEUTRAL = [
    "The {product} shipment of {qty} units is due on {date}.", "Sprint {int} closes on {weekday} with {int} stories open.",
    "Revenue grew {percent} in {quarter}, led by {city}.", "{company} renewed the contract for another year.",
    "The {team} team will present the roadmap at {time}.", "Deployment of {service} {version} to {env} took {ms} ms.",
    "Latency on {service} dropped by {percent} after the cache change.", "Please review the draft before {weekday}.",
    "The meeting moved to {time} on {weekday}.", "We onboarded {int} new vendors in {month}.",
    "The {city} office will be closed on {date}.", "Invoice totals for {month} came to {amount}.",
    "{company} asked for a revised quote on {product}.", "The audit found {int} minor issues and no major ones.",
    "Training sessions run every {weekday} at {time}.", "Backups completed with {int} files skipped.",
    "The {team} budget for {quarter} is {amount}.", "Stock of {product} is down to {qty} units.",
    "Customer satisfaction rose to {percent} this quarter.", "The release notes for {version} are on the wiki.",
    "Error rate on {service} stayed below {percent}.", "We shortlisted {int} candidates for the {team} role.",
    "Shipping from {city} takes about {int} hours.", "{company} confirmed the delivery window.",
    "The workshop agenda is attached.", "Let's sync after lunch.", "Thanks for the quick turnaround!",
    "Looping in the {team} team for visibility.", "The dashboard refreshes every {int} minutes.",
    "Our {city} warehouse handled {qty} orders yesterday.", "Payment terms remain net {int} days.",
    "The new policy takes effect on {date}.", "{int} tickets were closed this week.",
    "CPU usage on {env} peaked at {percent}.", "We are migrating {service} to the new cluster.",
    "The {product} vendor raised prices by {percent}.", "Kal ki meeting {time} baje hai.",
    "Sab theek hai, bas report pending hai.", "Please share the final deck by {weekday}.",
    "The contract value is {amount} over {int} months.", "Maintenance window is {weekday} {time}.",
    "All {int} test suites passed on {env}.", "The pilot in {city} ends on {date}.",
    "{company} is our largest client in {city}.", "Feedback from the {team} team was positive.",
    "Rollback of {service} was not needed.", "Average handling time is {int} minutes.",
    "We need {qty} more {product} by {date}.", "The office Wi-Fi will be upgraded on {weekday}.",
    "Our NPS for {quarter} is {int}.", "{service} restarted cleanly after the patch.",
    "Draft minutes from {weekday} are in the shared folder.", "Headcount for {team} stays at {int}.",
]

POS_KEYS = {
    "PERSON": ["name", "full_name", "customer_name", "Name", "holder", "nominee", "employee_name"],
    "EMAIL": ["email", "email_id", "mail", "contact_email", "user_email", "Email"],
    "PHONE": ["phone", "mobile", "contact_no", "phone_number", "mob", "Mobile No"],
    "ADDRESS": ["address", "addr", "residential_address", "ship_to", "Address"],
    "DATE_OF_BIRTH": ["dob", "date_of_birth", "birth_date", "DOB", "Date of Birth"],
    "AADHAAR": ["aadhaar", "aadhaar_no", "uid", "aadhar_number", "Aadhaar"],
    "PAN": ["pan", "pan_no", "pan_number", "PAN", "PAN No"],
    "CREDIT_CARD": ["card_number", "cc_number", "card_no", "Card", "credit_card"],
    "BANK_ACCOUNT": ["account_no", "acc_number", "bank_account", "beneficiary_account", "A/C No"],
    "IP_ADDRESS": ["ip", "client_ip", "host", "remote_addr", "server_ip", "IP"],
    "API_KEY": ["api_key", "token", "secret_key", "OPENAI_API_KEY", "stripe_key", "api_secret"],
    "PASSWORD": ["password", "pwd", "db_password", "pass", "admin_password", "Password"],
    "UPI_ID": ["upi_id", "vpa", "upi", "UPI ID", "payee_vpa"],
    "CVV": ["cvv", "cvv2", "cvc", "CVV", "security_code"],
    "CARD_EXPIRY": ["expiry", "exp", "card_expiry", "valid_thru", "Expiry"],
    "OTP_PIN": ["otp", "pin", "mpin", "upi_pin", "OTP", "atm_pin"],
    "USERNAME": ["username", "user", "login", "user_name", "Username", "login_id"],
    "AUTH_TOKEN": ["access_token", "auth_token", "refresh_token", "jwt", "session_token", "bearer"],
    "PRIVATE_KEY": ["private_key", "ssh_key", "tls_key", "PRIVATE_KEY", "signing_key"],
    "CONNECTION_STRING": ["database_url", "DATABASE_URL", "mongo_uri", "redis_url", "dsn", "conn_str"],
}

NEG_KEYS = {
    "order_id": "order_no", "awb": "awb", "tracking_no": "awb", "emp_id": "employee_id", "policy_no": "policy_no",
    "ticket_id": "ticket_no", "created_at": "epoch_ms", "updated_at": "epoch_ms", "upi_ref": "upi_ref",
    "utr": "upi_ref", "rrn": "rrn", "serial": "invoice_serial", "sku": "sku", "item_code": "sku",
    "gstin": "gstin", "pnr": "pnr", "coupon_code": "coupon", "card_last4": "masked_card", "voucher": "gift_card",
    "commit": "git_sha", "request_id": "uuid", "trace_id": "uuid", "checksum": "sha256",
    "integrity": "integrity_hash", "version": "version", "port": "port", "log_level": "config_value",
    "region": "config_value", "invoice_date": "event_date", "due_date": "event_date", "vendor": "company",
    "city": "city", "branch": "city", "office": "office_address", "pincode": "pincode", "ifsc": "ifsc",
    "hsn": "hsn", "amount": "amount", "qty": "quantity", "password_hint": "placeholder_password",
    "api_key_example": "example_key", "bind_host": "localhost",
    "status_code": "http_code", "room": "room_no", "offer_valid_till": "offer_validity", "mfg_exp": "offer_validity",
    "year": "year", "ext": "extension", "token_example": "placeholder_token", "run_as_user": "system_user",
    "db_user": "system_user", "public_key": "public_key", "ca_cert": "certificate", "ssh_pubkey": "ssh_pubkey",
    "cache_url": "conn_no_creds", "pin_code": "pincode",
}
