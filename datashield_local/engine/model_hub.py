"""Get the model onto this device: `datashield-local download-model`.

The model (~740 MB ONNX + tokenizer, labels, thresholds) lives in a Hugging Face model repo, not
in the Python package. It is downloaded once into <data dir>/models/<name> and every file is
checked against SHA256SUMS published with it, so a tampered or truncated download is refused.
"""
import hashlib
import os
from pathlib import Path

from datashield_local.engine.paths import MODEL_NAME, data_dir

# The published model repo;
# DATASHIELD_MODEL_REPO or --repo override it.
DEFAULT_REPO = os.environ.get("DATASHIELD_MODEL_REPO", "aniketClaude/datashield-local-v2.3")
SUMS_FILE = "SHA256SUMS"


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def write_sums(model_dir):
    """Create SHA256SUMS for a model folder (run before uploading it)."""
    model_dir = Path(model_dir)
    lines = [f"{sha256(p)}  {p.name}" for p in sorted(model_dir.iterdir())
             if p.is_file() and p.name != SUMS_FILE]
    (model_dir / SUMS_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def verify(model_dir):
    """Raise ValueError if any file listed in SHA256SUMS is missing or different."""
    model_dir = Path(model_dir)
    sums = model_dir / SUMS_FILE
    if not sums.exists():
        raise ValueError(f"{SUMS_FILE} missing in {model_dir}: refusing an unverified model")
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split(maxsplit=1)
        f = model_dir / name.strip()
        if not f.exists() or sha256(f) != digest:
            raise ValueError(f"checksum mismatch: {f.name}")


def download(repo=None, revision=None, dest=None):
    repo = repo or DEFAULT_REPO
    if not repo:
        raise SystemExit("no model repo set: pass --repo <hf-user>/<repo> or set DATASHIELD_MODEL_REPO")
    from huggingface_hub import snapshot_download
    dest = Path(dest) if dest else data_dir() / "models" / MODEL_NAME
    snapshot_download(repo_id=repo, revision=revision, local_dir=str(dest))
    verify(dest)
    return dest
