"""Turn files into text segments that remember where each character came from.

  extract_bytes(name, data) -> Extraction(segments, skipped)

Every Segment has the text to scan and enough information to map a character offset back to
a location: line (text, code, logs, CSV), page + line (PDF), paragraph (DOCX), sheet + cell
(XLSX), message part (.eml). Archives and e-mail attachments recurse; `member` is the path
inside them (report.zip > hr/salaries.xlsx).

Limits stop hostile inputs (zip bombs, huge files): see Limits. Anything not extracted is
listed in `skipped` with the reason, so a report never silently misses a file.
"""
import bisect
import email
import email.policy
import io
import re
import zipfile
from dataclasses import dataclass, field
from html import unescape
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

TEXT_EXT = {
    ".txt", ".log", ".md", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".conf", ".env", ".properties", ".xml", ".html", ".htm", ".sql", ".sh", ".bash", ".zsh", ".ps1",
    ".bat", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".cs",
    ".c", ".h", ".cpp", ".hpp", ".swift", ".scala", ".tf", ".tfvars", ".gradle", ".pem", ".key", ".pub",
    ".crt", ".npmrc", ".pypirc", ".netrc", ".dockerfile", ".gitconfig", ".rtf", ".srt", ".vtt",
}
TEXT_NAMES = {"dockerfile", "makefile", ".env", ".npmrc", ".netrc", ".pypirc", ".gitconfig", "id_rsa",
              "id_ed25519", "credentials", "config"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic"}
ZIP_EXT = {".zip", ".jar"}

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


@dataclass
class Limits:
    max_file_bytes: int = 50_000_000      # one file or archive member
    max_text_chars: int = 2_000_000       # text extracted from one file (rest is reported truncated)
    max_depth: int = 3                    # archives / attachments inside archives
    max_members: int = 2_000              # files inside one archive
    max_total_bytes: int = 300_000_000    # uncompressed bytes read from one archive tree
    max_ratio: int = 100                  # uncompressed / compressed, above = zip bomb


@dataclass
class Segment:
    text: str
    kind: str                     # text | pdf | docx | xlsx | eml
    member: str = ""              # path inside an archive / attachment chain
    page: int | None = None       # pdf
    part: str | None = None       # eml: headers / body / ...
    cells: list = field(default_factory=list)   # xlsx: [(start, end, sheet, ref)] sorted by start
    sheet: str | None = None
    _lines: list = field(default_factory=list, repr=False)

    def locate(self, start):
        """Where character `start` is, as a small dict for reports."""
        loc = {"member": self.member} if self.member else {}
        if self.kind == "xlsx":
            i = bisect.bisect_right([c[0] for c in self.cells], start) - 1
            if i >= 0:
                loc.update(sheet=self.cells[i][2], cell=self.cells[i][3])
            return loc
        if not self._lines:
            self._lines = [0] + [m.end() for m in re.finditer("\n", self.text)]
        line = bisect.bisect_right(self._lines, start)
        loc["paragraph" if self.kind == "docx" else "line"] = line
        loc["col"] = start - self._lines[line - 1] + 1
        if self.page is not None:
            loc["page"] = self.page
        if self.part:
            loc["part"] = self.part
        return loc


@dataclass
class Extraction:
    segments: list = field(default_factory=list)
    skipped: list = field(default_factory=list)     # {"member", "reason"}
    truncated: bool = False


def _decode(data):
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", "replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def looks_binary(data):
    head = data[:8192]
    if b"\x00" in head and not head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True
    ctrl = sum(b < 9 or 13 < b < 32 for b in head)
    return len(head) > 0 and ctrl / len(head) > 0.10


def _join(member, name):
    return f"{member} > {name}" if member else name


class _Extractor:
    def __init__(self, limits):
        self.lim = limits
        self.out = Extraction()
        self.total = 0
        self.chars = 0

    def add(self, seg):
        room = self.lim.max_text_chars - self.chars
        if room <= 0:
            self.out.truncated = True
            return
        if len(seg.text) > room:
            seg.text = seg.text[:room]
            self.out.truncated = True
        if seg.text.strip():
            self.chars += len(seg.text)
            self.out.segments.append(seg)

    def skip(self, member, reason):
        self.out.skipped.append({"member": member, "reason": reason})

    # ------------------------------------------------------------------ dispatch
    def run(self, name, data, member="", depth=0):
        self.total += len(data)
        if len(data) > self.lim.max_file_bytes:
            return self.skip(member or name, f"larger than {self.lim.max_file_bytes // 1_000_000} MB")
        if self.total > self.lim.max_total_bytes:
            return self.skip(member or name, "archive total size limit reached")
        p = PurePosixPath(name.replace("\\", "/"))
        ext, base = p.suffix.lower(), p.name.lower()
        try:
            if ext == ".pdf" or data[:5] == b"%PDF-":
                return self.pdf(data, member)
            if ext == ".docx":
                return self.docx(data, member)
            if ext in (".xlsx", ".xlsm"):
                return self.xlsx(data, member)
            if ext == ".eml":
                return self.eml(data, member, depth)
            if ext in ZIP_EXT:
                return self.zip(data, member, depth)
            if ext in IMAGE_EXT:
                return self.skip(member or name, "image (OCR not enabled)")
            if ext in TEXT_EXT or base in TEXT_NAMES or base.startswith(".env") or not looks_binary(data):
                text = _decode(data)
                if ext in (".html", ".htm"):
                    text = _html_text(text)
                return self.add(Segment(text, "text", member))
            return self.skip(member or name, "binary file")
        except zipfile.BadZipFile:
            return self.skip(member or name, "damaged or not a real archive/Office file")
        except Exception as e:  # one bad file must not stop a folder scan
            return self.skip(member or name, f"could not read: {type(e).__name__}")

    # ------------------------------------------------------------------ formats
    def pdf(self, data, member):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return self.skip(member or "pdf", "encrypted PDF")
        got = False
        for i, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            if text.strip():
                got = True
                self.add(Segment(text, "pdf", member, page=i))
        if not got:
            self.skip(member or "pdf", "PDF has no text layer (scanned image; OCR not enabled)")

    def docx(self, data, member):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            paras = []
            for part in ["word/document.xml"] + sorted(n for n in z.namelist()
                                                       if re.match(r"word/(header|footer)\d*\.xml$", n)):
                if part in z.namelist():
                    root = ET.fromstring(z.read(part))
                    for p in root.iter(f"{W}p"):
                        paras.append("".join(t.text or "" for t in p.iter(f"{W}t")))
        self.add(Segment("\n".join(paras), "docx", member))

    def xlsx(self, data, member):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
            shared = []
            if "xl/sharedStrings.xml" in names:
                for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{S}si"):
                    shared.append("".join(t.text or "" for t in si.iter(f"{S}t")))
            rels = {}
            if "xl/_rels/workbook.xml.rels" in names:
                for rel in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
                    rels[rel.get("Id")] = rel.get("Target").lstrip("/").removeprefix("xl/")
            for sh in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{S}sheet"):
                target = rels.get(sh.get(f"{R}id"))
                path = f"xl/{target}" if target else None
                if path not in names:
                    continue
                self._sheet(ET.fromstring(z.read(path)), sh.get("name"), shared, member)

    def _sheet(self, root, sheet, shared, member):
        # one line per row, cells joined by " | ": the table layout the model was trained on
        parts, cells, pos = [], [], 0
        for row in root.iter(f"{S}row"):
            line = []
            for c in row.iter(f"{S}c"):
                t, v = c.get("t"), c.find(f"{S}v")
                if t == "s" and v is not None:
                    val = shared[int(v.text)] if v.text and int(v.text) < len(shared) else ""
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(f"{S}t"))
                else:
                    val = v.text if v is not None and v.text else ""
                if val == "":
                    continue
                if line:
                    line.append(" | ")
                    pos += 3
                cells.append((pos, pos + len(val), sheet, c.get("r")))
                line.append(val)
                pos += len(val)
            if line:
                parts.append("".join(line) + "\n")
                pos += 1
        self.add(Segment("".join(parts), "xlsx", member, cells=cells, sheet=sheet))

    def eml(self, data, member, depth):
        msg = email.message_from_bytes(data, policy=email.policy.default)
        head = "\n".join(f"{k}: {msg[k]}" for k in ("From", "To", "Cc", "Subject") if msg[k])
        self.add(Segment(head, "text", member, part="headers"))
        for part in msg.walk():
            if part.is_multipart():
                continue
            fname = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if fname:
                if depth + 1 > self.lim.max_depth:
                    self.skip(_join(member, fname), "attachment nested too deep")
                else:
                    self.run(fname, payload, _join(member or "email", f"attachment:{fname}"), depth + 1)
            elif part.get_content_type() in ("text/plain", "text/html"):
                text = part.get_content() if isinstance(part.get_content(), str) else _decode(payload)
                if part.get_content_type() == "text/html":
                    text = _html_text(text)
                self.add(Segment(text, "text", member, part=part.get_content_type().split("/")[1]))

    def zip(self, data, member, depth):
        if depth + 1 > self.lim.max_depth:
            return self.skip(member or "zip", "archive nested too deep")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            if len(infos) > self.lim.max_members:
                self.skip(member or "zip", f"more than {self.lim.max_members} files; only the first are scanned")
                infos = infos[:self.lim.max_members]
            for info in infos:
                inner = _join(member, info.filename)
                if info.flag_bits & 0x1:
                    self.skip(inner, "encrypted archive member")
                    continue
                if info.compress_size and info.file_size / info.compress_size > self.lim.max_ratio:
                    self.skip(inner, "compression ratio too high (possible zip bomb)")
                    continue
                if info.file_size > self.lim.max_file_bytes:
                    self.skip(inner, f"larger than {self.lim.max_file_bytes // 1_000_000} MB")
                    continue
                if self.total + info.file_size > self.lim.max_total_bytes:
                    self.skip(inner, "archive total size limit reached")
                    break
                self.run(info.filename, z.read(info), inner, depth + 1)


_TAG = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.S | re.I)


def _html_text(html):
    return unescape(_TAG.sub(" ", html))


def extract_bytes(name, data, limits=None):
    ex = _Extractor(limits or Limits())
    ex.run(name, data)
    return ex.out
