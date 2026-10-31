"""Zip the browser extension for people to download and load into Chrome / Edge.

  python publish/build_extension_zip.py [--out DIR]

Writes dist/datashield-local-extension-v<version>.zip with the extension in a top-level
datashield-local/ folder (unzip, then "Load unpacked" on that folder). Left out: tests, test
fixtures and dev tools, and the local test page (127.0.0.1) from the sites list. The "key" stays,
so the extension ID matches the one the desktop engine's native host is registered for.
"""
import argparse
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extension"
FOLDER = "datashield-local"
SKIP_DIRS = {"fixtures", "tools", "node_modules"}
SKIP_NAMES = {"package.json", "README.md"}


def release_manifest():
    m = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    local = lambda patterns: [p for p in patterns if "127.0.0.1" not in p]
    m["host_permissions"] = local(m["host_permissions"])
    for cs in m["content_scripts"]:
        cs["matches"] = local(cs["matches"])
    return m


def files():
    for p in sorted(EXT.rglob("*")):
        rel = p.relative_to(EXT)
        if p.is_dir() or SKIP_DIRS & set(rel.parts) or p.name in SKIP_NAMES or p.name.endswith(".test.js"):
            continue
        if rel.as_posix() == "manifest.json":
            continue
        yield p, rel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    args = ap.parse_args()
    manifest = release_manifest()
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"datashield-local-extension-v{manifest['version']}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr(f"{FOLDER}/manifest.json", json.dumps(manifest, indent=2) + "\n")
        for p, rel in files():
            z.write(p, f"{FOLDER}/{rel.as_posix()}")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
