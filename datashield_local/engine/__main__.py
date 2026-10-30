"""Run the local detection engine.

  datashield-local                                    serve on 127.0.0.1:8765 (review app at http://127.0.0.1:8765)
  datashield-local download-model [--repo USER/REPO] get the model once (Hugging Face), checksum-verified
  datashield-local --model models/deberta_v1_onnx --port 8766
  datashield-local paths                              where the token, audit log, models and runtime file live
  datashield-local scan PATH [PATH ...] [--json out.json] [--html out.html] [--show-values] [--local]
  datashield-local install-native-host                let the browser extension reach the engine (this user only)
  datashield-local uninstall-native-host

From a source checkout use `python -m datashield_local ...` (or `python -m engine ...`) instead of `datashield-local`.

Clients find the engine through <data dir>/engine.json (port, pid, version) and authenticate
with the token in <data dir>/engine_secret.json (readable by this user only).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # source checkout: the folder holding datashield_local/

from datashield_local.feedback import DEFAULT_DB, FeedbackStore  # noqa: E402
from datashield_local.engine import ENGINE_VERSION  # noqa: E402
from datashield_local.engine.api import create_app  # noqa: E402
from datashield_local.engine.audit import AuditLog  # noqa: E402
from datashield_local.engine.core import Engine  # noqa: E402
from datashield_local.engine.paths import data_dir, default_model  # noqa: E402
from datashield_local.engine.security import SECRET_FILE, load_or_create_secrets  # noqa: E402
from datashield_local.policy import DEFAULT_POLICY  # noqa: E402

RUNTIME_FILE = "engine.json"
DEFAULT_MODEL = default_model()


def build(model, db=DEFAULT_DB, policy=DEFAULT_POLICY, port=8765, ddir=None, predictor=None,
          allowed_hosts=None):
    """Engine + app, wired to the per-device data folder. Returns (app, engine, data_dir)."""
    ddir = Path(ddir) if ddir else data_dir()
    sec = load_or_create_secrets(ddir)
    audit = AuditLog(ddir / "audit" / "audit.jsonl", sec["salt"])
    engine = Engine(model, policy, audit=audit, predictor=predictor)
    app = create_app(engine, FeedbackStore(db), sec["token"], port, allowed_hosts)
    return app, engine, ddir


def main():
    ap = argparse.ArgumentParser(description="DataShield Local engine")
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "paths", "scan", "download-model", "install-native-host",
                                     "uninstall-native-host"])
    ap.add_argument("paths", nargs="*", help="scan: files or folders")
    ap.add_argument("--json", help="scan: write a JSON report here")
    ap.add_argument("--html", help="scan: write an HTML report here")
    ap.add_argument("--show-values", action="store_true", help="scan: print values, not masked previews")
    ap.add_argument("--local", action="store_true", help="scan: load the model here even if the engine runs")
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--db", default=str(DEFAULT_DB), help="local feedback database")
    ap.add_argument("--policy", default=str(DEFAULT_POLICY), help="severity/action policy (YAML)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--repo", help="download-model: Hugging Face repo id (default: DATASHIELD_MODEL_REPO)")
    ap.add_argument("--revision", help="download-model: tag / commit to pin")
    args = ap.parse_args()

    if args.command == "download-model":
        from datashield_local.engine.model_hub import download
        print(f"model ready: {download(args.repo, args.revision)}")
        return

    if args.command == "scan":
        from datashield_local.engine.scan_cli import run
        sys.exit(run(args.paths, args.json, args.html, args.show_values, args.local, args.model))

    if args.command in ("install-native-host", "uninstall-native-host"):
        from datashield_local.engine import install_host
        fn = install_host.install if args.command.startswith("install") else install_host.uninstall
        print(json.dumps(fn(), indent=2))
        return

    ddir = data_dir()
    if args.command == "paths":
        print(json.dumps({"data_dir": str(ddir), "token_file": str(ddir / SECRET_FILE),
                          "runtime_file": str(ddir / RUNTIME_FILE),
                          "audit_log": str(ddir / "audit" / "audit.jsonl")}, indent=2))
        return

    if not (Path(args.model) / "model.onnx").exists():
        sys.exit(f"no model at {args.model}\nrun `datashield-local download-model` first (or pass --model / set DATASHIELD_MODEL)")
    import uvicorn
    app, engine, ddir = build(args.model, args.db, args.policy, args.port)
    engine.scan("warm up", source="startup", audit=False)
    runtime = ddir / RUNTIME_FILE
    runtime.write_text(json.dumps({"port": args.port, "pid": os.getpid(), "engine": ENGINE_VERSION,
                                   "model_id": engine.model_id, "started": time.time()}), encoding="utf-8")
    print(f"\n  DataShield Local engine {ENGINE_VERSION} · model {engine.model_id}")
    print(f"  review app  http://127.0.0.1:{args.port}")
    print(f"  data dir    {ddir}\n")
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        runtime.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
