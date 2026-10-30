"""File extraction: every format gives text with the right location, hostile inputs are skipped."""
import base64
import io
import json
import shutil
import zipfile
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from datashield_local.engine.__main__ import build
from datashield_local.engine.extract import Limits, extract_bytes
from tests.test_engine import StubPredictor

CARD = "4111 1111 1111 1111"


def docx_bytes(paragraphs):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>' for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def xlsx_bytes(rows, sheet="Payments"):
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    strings = [v for row in rows for v in row]
    sst = "".join(f"<si><t>{s}</t></si>" for s in strings)
    xml_rows, k = [], 0
    for ri, row in enumerate(rows, 1):
        cells = []
        for ci, _ in enumerate(row):
            cells.append(f'<c r="{"ABCDEFG"[ci]}{ri}" t="s"><v>{k}</v></c>')
            k += 1
        xml_rows.append(f'<row r="{ri}">{"".join(cells)}</row>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="{r}"><sheets>'
                                      f'<sheet name="{sheet}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}">{sst}</sst>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{ns}"><sheetData>{"".join(xml_rows)}'
                                               f'</sheetData></worksheet>')
    return buf.getvalue()


def pdf_bytes(lines):
    """A minimal one-page PDF with a text layer (no PDF library needed)."""
    ops = " ".join(f"BT /F1 12 Tf 72 {720 - 20 * i} Td ({t}) Tj ET" for i, t in enumerate(lines))
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(ops)} >>\nstream\n{ops}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def zip_bytes(files, compress=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compress) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def texts(ex):
    return "\n".join(s.text for s in ex.segments)


def test_text_lines_and_columns():
    ex = extract_bytes("notes.txt", f"hello\nmy card {CARD}\n".encode())
    seg = ex.segments[0]
    assert seg.locate(seg.text.index(CARD)) == {"line": 2, "col": 9}


def test_docx_paragraphs():
    ex = extract_bytes("memo.docx", docx_bytes(["Intro", f"Card {CARD} on file"]))
    seg = ex.segments[0]
    assert seg.locate(seg.text.index(CARD))["paragraph"] == 2


def test_xlsx_cells_keep_header_context():
    ex = extract_bytes("pay.xlsx", xlsx_bytes([["name", "card_number"], ["Rahul", CARD]]))
    seg = ex.segments[0]
    assert seg.text.splitlines() == ["name | card_number", f"Rahul | {CARD}"]
    assert seg.locate(seg.text.index(CARD)) == {"sheet": "Payments", "cell": "B2"}


def test_pdf_pages():
    ex = extract_bytes("stmt.pdf", pdf_bytes(["Statement", f"Card {CARD}"]))
    assert CARD in texts(ex)
    assert ex.segments[0].locate(0)["page"] == 1


def test_eml_body_and_attachment():
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "a@x.com", "b@y.com", "card details"
    msg.set_content(f"Use card {CARD}")
    msg.add_attachment(b"DB_PASSWORD=Summer@2024\n", maintype="text", subtype="plain", filename="prod.env")
    ex = extract_bytes("mail.eml", bytes(msg))
    parts = {s.part or s.member for s in ex.segments}
    assert "headers" in parts and "plain" in parts and "email > attachment:prod.env" in parts
    assert "Summer@2024" in texts(ex)


def test_zip_recurses_and_reports_members():
    inner = zip_bytes({"config/.env": b"API_KEY=sk-abc\n"})
    ex = extract_bytes("bundle.zip", zip_bytes({"readme.md": b"hi", "nested.zip": inner,
                                                 "logo.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 64}))
    members = {s.member for s in ex.segments}
    assert "nested.zip > config/.env" in members
    assert {"member": "logo.png", "reason": "image (OCR not enabled)"} in ex.skipped


def test_zip_bomb_and_depth_limits():
    bomb = zip_bytes({"big.txt": b"0" * 5_000_000})
    ex = extract_bytes("bomb.zip", bomb)
    assert ex.segments == [] and "zip bomb" in ex.skipped[0]["reason"]
    deep = b"secret"
    for i in range(5):
        deep = zip_bytes({f"l{i}.zip" if i else "x.txt": deep})
    ex = extract_bytes("deep.zip", deep, Limits(max_depth=3))
    assert any("too deep" in s["reason"] for s in ex.skipped)


def test_binary_and_damaged_files_are_skipped_not_fatal():
    assert extract_bytes("a.bin", bytes(range(256)) * 10).skipped[0]["reason"] == "binary file"
    assert "damaged" in extract_bytes("bad.docx", b"not a zip").skipped[0]["reason"]


@pytest.fixture()
def client(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    shutil.copy("configs/labels_v2.yaml", model / "labels.yaml")
    app, engine, ddir = build(model, db=tmp_path / "fb.db", ddir=tmp_path / "data", port=8765,
                              predictor=StubPredictor())
    token = json.loads((ddir / "engine_secret.json").read_text())["token"]
    return TestClient(app, base_url="http://127.0.0.1:8765"), {"Authorization": f"Bearer {token}"}, ddir


def test_scan_upload_and_file_give_locations_and_one_assessment(client, tmp_path):
    c, auth, ddir = client
    data = xlsx_bytes([["name", "card", "cvv"], ["Rahul", CARD, "123"]])
    body = {"name": "pay.xlsx", "data_b64": base64.b64encode(data).decode()}
    r = c.post("/v1/scan-upload", json=body, headers=auth).json()
    locs = {f["label"]: f["location"] for f in r["findings"]}
    assert locs["CREDIT_CARD"] == {"sheet": "Payments", "cell": "B2"} and locs["CVV"]["cell"] == "C2"
    assert r["risk"] == "critical" and "card_kit" in [x["name"] for x in r["combos"]]   # across cells
    assert all("value" not in f for f in r["findings"]) and r["findings"][0]["preview"].count("•") > 0
    raw = (ddir / "audit" / "audit.jsonl").read_text()
    assert "4111" not in raw and json.loads(raw.splitlines()[-1])["findings"][0]["loc"]["sheet"] == "Payments"
    p = tmp_path / "notes.txt"
    p.write_text(f"line one\ncard {CARD}\n")
    r = c.post("/v1/scan-file", json={"path": str(p)}, headers=auth).json()
    assert r["findings"][0]["location"] == {"line": 2, "col": 6}
    assert c.post("/v1/scan-file", json={"path": str(p)}).status_code == 401
