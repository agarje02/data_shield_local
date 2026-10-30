"""HTTP API of the engine (127.0.0.1 only; access rules in engine/security.py).

/v1/*   for programs: browser-extension native host, endpoint agent, CLI. Bearer token.
        GET  /v1/health     liveness + model id (no token)
        POST /v1/scan       {text, source, context} -> spans, assessment, masked, ms
        POST /v1/scan-file  {path} -> findings with locations (any supported format, archives)
        POST /v1/scan-upload {name, data_b64} -> same, for bytes a client already holds
        POST /v1/assess     {spans} -> assessment (risk for an edited span list)
        GET  /v1/policy     severity / action / combos in use
        POST /v1/feedback   reviewed result -> local feedback store
        GET  /v1/audit      latest audit events (labels and hashes, no values)
/api/*  for the review app page served at / by this same engine (same-origin only).
"""
import base64
import binascii
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from datashield_local.engine import ENGINE_VERSION
from datashield_local.engine.security import install_guard

STATIC = Path(__file__).resolve().parents[1] / "static"
MAX_UPLOAD = 25_000_000


class ScanIn(BaseModel):
    text: str
    source: str = "client"
    context: dict = Field(default_factory=dict)
    thresholds: bool = True
    validators: bool = True
    audit: Literal["all", "findings"] = "all"     # "findings": log only when something was found


class Span(BaseModel):
    start: int
    end: int
    label: str
    score: float | None = None
    source: str | None = None


class FileIn(BaseModel):
    path: str
    include_values: bool = False
    context: dict = Field(default_factory=dict)


class UploadIn(BaseModel):
    name: str
    data_b64: str
    include_values: bool = False
    source: str = "upload"
    context: dict = Field(default_factory=dict)


class AssessIn(BaseModel):
    spans: list[Span]


class FeedbackIn(BaseModel):
    text: str
    predicted: list[Span]
    corrected: list[Span]
    note: str = ""
    source: str = "typed"


def create_app(engine, store, token, port, allowed_hosts=None):
    app = FastAPI(title="DataShield Local engine", docs_url=None, redoc_url=None, openapi_url=None)
    install_guard(app, token, port, **({"allowed_hosts": allowed_hosts} if allowed_hosts else {}))

    def do_scan(body, source=None):
        try:
            return engine.scan(body.text, source=source or body.source, context=body.context,
                               thresholds=body.thresholds, validators=body.validators,
                               audit=True if body.audit == "all" else "findings")
        except ValueError as e:
            raise HTTPException(413, str(e))

    def do_upload(body, source=None):
        """File bytes sent by a client (browser upload, review app), base64 in JSON."""
        try:
            data = base64.b64decode(body.data_b64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400, "data_b64 is not valid base64")
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, f"file larger than {MAX_UPLOAD // 1_000_000} MB")
        return engine.scan_document(Path(body.name).name, data, source=source or body.source,
                                    context=body.context, include_values=body.include_values)

    def do_feedback(body):
        try:
            fid, diff = store.add(body.text, [s.model_dump() for s in body.predicted],
                                  [s.model_dump() for s in body.corrected], body.note, engine.model_id,
                                  body.source)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"id": fid, "diff": {k: len(v) for k, v in diff.items()}}

    # ---------------------------------------------------------------- /v1: programs
    @app.get("/v1/health")
    def health():
        return {"status": "ok", "engine": ENGINE_VERSION, "model_id": engine.model_id,
                "uptime_s": round(time.time() - engine.started), "scans": engine.scans}

    @app.post("/v1/scan")
    def v1_scan(body: ScanIn):
        return do_scan(body)

    @app.post("/v1/scan-file")
    def v1_scan_file(body: FileIn):
        """A file on this machine (the caller already has the user's file access)."""
        p = Path(body.path)
        if not p.is_file():
            raise HTTPException(404, "not a file")
        return engine.scan_path(p, include_values=body.include_values, context=body.context)

    @app.post("/v1/scan-upload")
    def v1_scan_upload(body: UploadIn):
        return do_upload(body)

    @app.post("/v1/assess")
    def v1_assess(body: AssessIn):
        return engine.policy.assess([s.model_dump() for s in body.spans])

    @app.get("/v1/policy")
    def v1_policy():
        return engine.policy.to_dict()

    @app.post("/v1/feedback")
    def v1_feedback(body: FeedbackIn):
        return do_feedback(body)

    @app.get("/v1/audit")
    def v1_audit(limit: int = 50):
        return {"events": engine.audit.tail(limit) if engine.audit else []}

    # ---------------------------------------------------------------- /api: review app page
    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/ext-test")
    def ext_test():
        """Local page the browser extension also runs on, for testing it without real sites."""
        return FileResponse(STATIC / "ext_test.html")

    @app.get("/api/info")
    def info():
        return {**engine.info(), "engine": ENGINE_VERSION, "feedback_db": str(store.path)}

    @app.post("/api/scan")
    def api_scan(body: ScanIn):
        return do_scan(body, source="review-app")

    @app.post("/api/scan-upload")
    def api_scan_upload(body: UploadIn):
        return do_upload(body, source="review-app")

    @app.post("/api/assess")
    def api_assess(body: AssessIn):
        return engine.policy.assess([s.model_dump() for s in body.spans])

    @app.post("/api/feedback")
    def api_feedback(body: FeedbackIn):
        return do_feedback(body)

    @app.get("/api/feedback")
    def list_feedback(limit: int = 50):
        return {"stats": store.stats(), "recent": store.recent(limit)}

    @app.delete("/api/feedback/{fid}")
    def delete_feedback(fid: int):
        if not store.delete(fid):
            raise HTTPException(404, "not found")
        return {"deleted": fid}

    @app.get("/api/feedback/export")
    def export_feedback():
        return PlainTextResponse(store.export_jsonl(), media_type="application/x-ndjson",
                                 headers={"Content-Disposition": "attachment; filename=feedback.jsonl"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
