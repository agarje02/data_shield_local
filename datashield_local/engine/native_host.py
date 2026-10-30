"""Native-messaging host: the browser extension's only way into the engine.

Chrome / Edge start this program and talk to it over stdin/stdout (4-byte little-endian length,
then UTF-8 JSON). It relays each request to the local engine with the device token, so web
pages never get a route to the engine and the extension never sees the token.

  request   {"id": 7, "op": "health" | "scan" | "upload" | "feedback", ...fields}
  response  {"id": 7, "ok": true, "result": {...}}   or   {"id": 7, "ok": false, "error": "engine_down"}

Standard library only, so it starts fast. Installed with `python -m engine install-native-host`.
"""
import json
import socket
import struct
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # source checkout: the folder holding datashield_local/

from datashield_local.engine.paths import data_dir  # noqa: E402

HOST_NAME = "com.datashield.engine"
MAX_OUT = 1_000_000          # Chrome rejects host -> extension messages over 1 MB
OPS = {"health": ("GET", "/v1/health", 5), "scan": ("POST", "/v1/scan", 60),
       "upload": ("POST", "/v1/scan-upload", 300), "feedback": ("POST", "/v1/feedback", 30)}


def read_message(stream):
    head = stream.read(4)
    if len(head) < 4:
        return None
    (n,) = struct.unpack("<I", head)
    return json.loads(stream.read(n).decode("utf-8"))


def write_message(stream, msg):
    data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_OUT:
        data = json.dumps({"id": msg.get("id"), "ok": False, "error": "response_too_large"}).encode()
    stream.write(struct.pack("<I", len(data)) + data)
    stream.flush()


def engine_target(ddir):
    """(base url, token) of the running engine, or None."""
    try:
        port = json.loads((ddir / "engine.json").read_text())["port"]
        token = json.loads((ddir / "engine_secret.json").read_text())["token"]
        return f"http://127.0.0.1:{port}", token
    except (OSError, KeyError, ValueError):
        return None


def listening(base_url, timeout=0.3):
    """Quick port check. A stale engine.json (engine killed) would otherwise cost ~2 s per request:
    Windows retries a refused connection to localhost before giving up."""
    try:
        socket.create_connection(("127.0.0.1", urlsplit(base_url).port), timeout=timeout).close()
        return True
    except OSError:
        return False


def handle(msg, ddir):
    rid, op = msg.get("id"), msg.get("op")
    if op not in OPS:
        return {"id": rid, "ok": False, "error": "unknown_op"}
    target = engine_target(ddir)
    if target is None or not listening(target[0]):
        return {"id": rid, "ok": False, "error": "engine_down"}
    method, path, timeout = OPS[op]
    body = {k: v for k, v in msg.items() if k not in ("id", "op")}
    req = urllib.request.Request(target[0] + path, method=method,
                                 data=json.dumps(body).encode() if method == "POST" else None,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {target[1]}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"id": rid, "ok": True, "result": json.loads(r.read())}
    except urllib.error.HTTPError as e:
        return {"id": rid, "ok": False, "error": f"http_{e.code}"}
    except (urllib.error.URLError, OSError):
        return {"id": rid, "ok": False, "error": "engine_down"}


def main(stdin=None, stdout=None):
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    ddir = data_dir()
    while True:
        msg = read_message(stdin)
        if msg is None:          # browser closed the port
            return
        write_message(stdout, handle(msg, ddir))


if __name__ == "__main__":
    main()
