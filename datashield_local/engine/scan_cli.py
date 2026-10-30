"""Scan files and folders from the command line.

  python -m engine scan PATH [PATH ...] [--json out.json] [--html out.html] [--show-values] [--local]

Uses the running engine when there is one (the model stays loaded once); otherwise loads the
model in this process. Folders are walked recursively, skipping .git, node_modules, virtualenvs
and build output; symlinks are not followed. Reports show a masked preview of each value
(41•••••11) unless --show-values is given.
"""
import html
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from datashield_local.engine.paths import data_dir
from datashield_local.engine.security import SECRET_FILE

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__", ".idea", ".vscode",
             "dist", "build", ".tox", ".mypy_cache", ".pytest_cache", ".next", "target"}
LEVEL_ORDER = ["critical", "high", "medium", "low", "none"]


def walk(paths):
    for p in map(Path, paths):
        if p.is_file():
            yield p
        elif p.is_dir():
            for root, dirs, files in os.walk(p, followlinks=False):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                for f in sorted(files):
                    fp = Path(root) / f
                    if fp.is_file() and not fp.is_symlink():
                        yield fp


class RemoteEngine:
    """The running engine over its /v1 API."""

    def __init__(self, port, token):
        self.base, self.auth = f"http://127.0.0.1:{port}", f"Bearer {token}"

    @classmethod
    def find(cls):
        d = data_dir()
        try:
            port = json.loads((d / "engine.json").read_text())["port"]
            token = json.loads((d / SECRET_FILE).read_text())["token"]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/health", timeout=2) as r:
                if json.loads(r.read())["status"] == "ok":
                    return cls(port, token)
        except (OSError, KeyError, ValueError, urllib.error.URLError):
            pass
        return None

    def scan_path(self, path, include_values=False):
        req = urllib.request.Request(f"{self.base}/v1/scan-file", method="POST",
                                     data=json.dumps({"path": str(Path(path).resolve()),
                                                      "include_values": include_values}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": self.auth})
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.loads(r.read())


def location_text(loc):
    parts = []
    if loc.get("member"):
        parts.append(loc["member"])
    if "sheet" in loc:
        parts.append(f"{loc['sheet']}!{loc.get('cell', '')}")
    if "page" in loc:
        parts.append(f"page {loc['page']}")
    if "part" in loc:
        parts.append(loc["part"])
    for k in ("line", "paragraph"):
        if k in loc:
            parts.append(f"{k} {loc[k]}")
    return ", ".join(parts)


def html_report(results, took):
    color = {"critical": "#c62828", "high": "#e8590c", "medium": "#b7791f", "low": "#607d8b", "none": "#2e7d32"}
    rows = []
    for r in results:
        badge = f'<span class="b" style="background:{color[r["risk"]]}">{r["risk"]}</span>'
        rows.append(f'<h3>{badge} {html.escape(r["name"])} <small>{len(r["findings"])} finding(s), '
                    f'{r["ms"] / 1000:.1f} s</small></h3>')
        if r["combos"]:
            rows.append("<p>" + "<br>".join(f'<b>{html.escape(c["name"])}</b>: {html.escape(c["reason"])}'
                                            for c in r["combos"]) + "</p>")
        if r["findings"]:
            rows.append("<table><tr><th>Severity</th><th>Type</th><th>Where</th><th>Value</th></tr>" + "".join(
                f'<tr><td><span class="b" style="background:{color[f["severity"]]}">{f["severity"]}</span></td>'
                f'<td>{f["label"]}</td><td>{html.escape(location_text(f["location"]))}</td>'
                f'<td><code>{html.escape(f.get("value", f["preview"]))}</code></td></tr>' for f in r["findings"])
                + "</table>")
        for s in r["skipped"]:
            rows.append(f'<p class="skip">skipped {html.escape(s["member"])}: {html.escape(s["reason"])}</p>')
    flagged = sum(1 for r in results if r["findings"])
    return f"""<!doctype html><meta charset="utf-8"><title>Sensitive data scan</title>
<style>body{{font:14px system-ui,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#1d2433}}
table{{border-collapse:collapse;width:100%;margin:6px 0 18px}}td,th{{border-bottom:1px solid #e3e6eb;padding:5px 8px;text-align:left}}
.b{{color:#fff;border-radius:4px;padding:1px 7px;font-size:11px;font-weight:700;text-transform:uppercase}}
h3{{margin:22px 0 4px;font-size:15px}}small,.skip{{color:#667085;font-weight:400}}code{{font-size:12px}}</style>
<h1>Sensitive data scan</h1><p>{len(results)} file(s) scanned in {took:.1f} s · {flagged} with findings ·
generated {time.strftime('%Y-%m-%d %H:%M')}</p>{''.join(rows)}"""


def run(paths, json_out=None, html_out=None, show_values=False, local=False, model=None):
    files = list(walk(paths))
    if not files:
        print("no files found", file=sys.stderr)
        return 1
    engine = None if local else RemoteEngine.find()
    if engine:
        print(f"using the running engine at {engine.base}", file=sys.stderr)
    else:
        from datashield_local.engine.__main__ import DEFAULT_MODEL
        from datashield_local.engine.audit import AuditLog
        from datashield_local.engine.core import Engine
        from datashield_local.engine.security import load_or_create_secrets
        from datashield_local.policy import DEFAULT_POLICY
        print("loading the model in this process…", file=sys.stderr)
        d = data_dir()
        audit = AuditLog(d / "audit" / "audit.jsonl", load_or_create_secrets(d)["salt"])
        engine = Engine(model or DEFAULT_MODEL, DEFAULT_POLICY, audit=audit)
    t0, results = time.perf_counter(), []
    for i, f in enumerate(files, 1):
        r = engine.scan_path(f, include_values=show_values)
        results.append(r)
        mark = "" if r["risk"] == "none" else f"  [{r['risk'].upper()}] {len(r['findings'])} finding(s)"
        print(f"[{i}/{len(files)}] {f}{mark}", flush=True)
        for fd in r["findings"]:
            val = fd.get("value", fd["preview"])
            print(f"      {fd['severity']:<8} {fd['label']:<18} {location_text(fd['location']):<30} {val}")
        for s in r["skipped"]:
            print(f"      skipped {Path(s['member']).name if s['member'] == str(f) else s['member']}: {s['reason']}")
    took = time.perf_counter() - t0
    by = {lvl: sum(r["risk"] == lvl for r in results) for lvl in LEVEL_ORDER}
    print(f"\n{len(files)} file(s) in {took:.1f} s · " + " · ".join(f"{n} {k}" for k, n in by.items() if n))
    if json_out:
        Path(json_out).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"JSON report: {json_out}")
    if html_out:
        Path(html_out).write_text(html_report(results, took), encoding="utf-8")
        print(f"HTML report: {html_out}")
    return 2 if by["critical"] else 0
