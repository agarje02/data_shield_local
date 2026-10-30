"""Who may talk to the engine.

The engine listens on 127.0.0.1 only. On top of that:
  - Host header must name this machine (127.0.0.1 / localhost / [::1]); this stops DNS
    rebinding, where a web page's own domain is pointed at 127.0.0.1.
  - A browser Origin header is accepted only from the engine's own page (the review app);
    any other web page is refused.
  - /v1/* (programs: extension native host, agent, CLI) needs the per-device bearer token,
    stored in a file only this user can read. /v1/health is open so clients can probe.
"""
import hmac
import json
import os
import secrets
from pathlib import Path

from fastapi.responses import JSONResponse

SECRET_FILE = "engine_secret.json"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


def load_or_create_secrets(data_dir):
    """{"token": ..., "salt": ...}; created on first run, kept across restarts."""
    path = Path(data_dir) / SECRET_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    sec = {"token": secrets.token_urlsafe(32), "salt": secrets.token_hex(16)}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # user-only on macOS/Linux
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(sec, f)
    return sec


def _hostname(host_header):
    host = host_header.strip().lower()
    if host.startswith("["):                      # [::1]:8765
        return host[:host.index("]") + 1]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def install_guard(app, token, port, allowed_hosts=LOCAL_HOSTS, open_paths=("/v1/health",)):
    own_origins = {f"http://{h}:{port}" for h in allowed_hosts}

    @app.middleware("http")
    async def guard(request, call_next):
        if _hostname(request.headers.get("host", "")) not in allowed_hosts:
            return JSONResponse({"detail": "host not allowed"}, status_code=403)
        origin = request.headers.get("origin")
        if origin is not None and origin not in own_origins:
            return JSONResponse({"detail": "origin not allowed"}, status_code=403)
        path = request.url.path
        if path.startswith("/v1/") and path not in open_paths:
            auth = request.headers.get("authorization", "")
            if not hmac.compare_digest(auth.encode(), f"Bearer {token}".encode()):
                return JSONResponse({"detail": "missing or wrong token"}, status_code=401)
        return await call_next(request)
