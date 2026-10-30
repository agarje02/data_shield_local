import io
import json
import time

from datashield_local.engine import native_host as nh
from datashield_local.engine.install_host import extension_id


def test_message_framing_roundtrip():
    buf = io.BytesIO()
    nh.write_message(buf, {"id": 1, "ok": True, "result": {"text": "héllo"}})
    buf.seek(0)
    assert nh.read_message(buf) == {"id": 1, "ok": True, "result": {"text": "héllo"}}
    assert nh.read_message(io.BytesIO(b"")) is None      # browser closed the port


def test_too_large_response_becomes_an_error():
    buf = io.BytesIO()
    nh.write_message(buf, {"id": 2, "ok": True, "result": "x" * (nh.MAX_OUT + 10)})
    buf.seek(0)
    assert nh.read_message(buf) == {"id": 2, "ok": False, "error": "response_too_large"}


def test_engine_down_and_unknown_op(tmp_path):
    assert nh.handle({"id": 3, "op": "scan", "text": "x"}, tmp_path) == {"id": 3, "ok": False, "error": "engine_down"}
    assert nh.handle({"id": 4, "op": "rm -rf"}, tmp_path)["error"] == "unknown_op"


def test_stale_runtime_file_fails_fast(tmp_path):
    # engine killed: engine.json still names a port, but nothing listens there
    (tmp_path / "engine.json").write_text(json.dumps({"port": 1}))
    (tmp_path / "engine_secret.json").write_text(json.dumps({"token": "t"}))
    t0 = time.perf_counter()
    assert nh.handle({"id": 5, "op": "scan", "text": "x"}, tmp_path)["error"] == "engine_down"
    assert time.perf_counter() - t0 < 1.0


def test_main_loop_answers_until_eof(tmp_path, monkeypatch):
    monkeypatch.setattr(nh, "data_dir", lambda: tmp_path)
    inp = io.BytesIO()
    for i in (1, 2):
        nh.write_message(inp, {"id": i, "op": "health"})
    inp.seek(0)
    out = io.BytesIO()
    nh.main(inp, out)
    out.seek(0)
    assert [nh.read_message(out)["id"] for _ in range(2)] == [1, 2]


def test_extension_id_matches_manifest_key():
    key = json.load(open("extension/manifest.json"))["key"]
    eid = extension_id(key)
    assert len(eid) == 32 and set(eid) <= set("abcdefghijklmnop")
