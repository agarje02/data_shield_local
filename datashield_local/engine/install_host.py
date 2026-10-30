"""Register the native-messaging host with Chrome and Edge, for the current user only.

  datashield-local install-native-host      (or: uninstall-native-host; from source: python -m datashield_local ...)

Writes <data dir>/native/{launcher, com.datashield.engine.json} and points each browser at it:
  Windows  HKCU\\Software\\Google\\Chrome\\NativeMessagingHosts\\com.datashield.engine  (and Microsoft\\Edge)
  macOS    ~/Library/Application Support/{Google/Chrome, Microsoft Edge}/NativeMessagingHosts/
  Linux    ~/.config/{google-chrome, microsoft-edge}/NativeMessagingHosts/
Only the extension whose ID is derived from extension/manifest.json "key" may connect.
"""
import base64
import hashlib
import json
import sys
from pathlib import Path

from datashield_local.engine.native_host import HOST_NAME
from datashield_local.engine.paths import data_dir

ROOT = Path(__file__).resolve().parents[2]
EXT_MANIFEST = ROOT / "extension" / "manifest.json"     # source checkout only
# IDs allowed to talk to the engine: the developer build (fixed by the manifest "key"); add the
# Chrome Web Store / Edge Add-ons IDs here once the extension is published there.
KNOWN_EXTENSION_IDS = ["kemadmlodfnjgebighmaflcgieihckka"]
REG_KEYS = {"chrome": r"Software\Google\Chrome\NativeMessagingHosts",
            "edge": r"Software\Microsoft\Edge\NativeMessagingHosts"}
UNIX_DIRS = {
    "darwin": {"chrome": "~/Library/Application Support/Google/Chrome/NativeMessagingHosts",
               "edge": "~/Library/Application Support/Microsoft Edge/NativeMessagingHosts"},
    "linux": {"chrome": "~/.config/google-chrome/NativeMessagingHosts",
              "edge": "~/.config/microsoft-edge/NativeMessagingHosts"},
}


def extension_id(public_key_b64):
    """Chrome's extension ID: first 32 hex chars of sha256(public key DER), mapped 0-f -> a-p."""
    digest = hashlib.sha256(base64.b64decode(public_key_b64)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def allowed_extension_ids():
    """Known IDs, plus the source checkout's own extension when there is one."""
    ids = list(KNOWN_EXTENSION_IDS)
    if EXT_MANIFEST.exists():
        own = extension_id(json.loads(EXT_MANIFEST.read_text(encoding="utf-8"))["key"])
        ids = [own] + [i for i in ids if i != own]
    return ids


def _launcher(native_dir):
    script = Path(__file__).resolve().parent / "native_host.py"
    if sys.platform == "win32":
        path = native_dir / "sdd_native_host.bat"
        path.write_text(f'@echo off\r\n"{sys.executable}" -u "{script}" %*\r\n', encoding="utf-8")
    else:
        path = native_dir / "sdd_native_host.sh"
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" -u "{script}" "$@"\n', encoding="utf-8")
        path.chmod(0o700)
    return path


def install(browsers=("chrome", "edge")):
    native_dir = data_dir() / "native"
    native_dir.mkdir(parents=True, exist_ok=True)
    ids = allowed_extension_ids()
    manifest = {"name": HOST_NAME, "description": "DataShield Local engine bridge",
                "path": str(_launcher(native_dir)), "type": "stdio",
                "allowed_origins": [f"chrome-extension://{i}/" for i in ids]}
    mpath = native_dir / f"{HOST_NAME}.json"
    mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    done = []
    if sys.platform == "win32":
        import winreg
        for b in browsers:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{REG_KEYS[b]}\{HOST_NAME}") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, str(mpath))
            done.append(rf"HKCU\{REG_KEYS[b]}\{HOST_NAME}")
    else:
        for b in browsers:
            d = Path(UNIX_DIRS["darwin" if sys.platform == "darwin" else "linux"][b]).expanduser()
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{HOST_NAME}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            done.append(str(d / f"{HOST_NAME}.json"))
    return {"extension_ids": ids, "manifest": str(mpath), "registered": done}


def uninstall(browsers=("chrome", "edge")):
    removed = []
    if sys.platform == "win32":
        import winreg
        for b in browsers:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"{REG_KEYS[b]}\{HOST_NAME}")
                removed.append(b)
            except FileNotFoundError:
                pass
    else:
        for b in browsers:
            p = Path(UNIX_DIRS["darwin" if sys.platform == "darwin" else "linux"][b]).expanduser() / f"{HOST_NAME}.json"
            if p.exists():
                p.unlink()
                removed.append(b)
    return {"removed": removed}
