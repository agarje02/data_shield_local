"""Where the engine keeps per-device state (token, audit log, runtime file).

DATASHIELD_DATA_DIR overrides; otherwise the usual per-user app-data folder of each OS."""
import os
import sys
from pathlib import Path

APP_NAME = "DataShieldLocal"


def data_dir():
    if os.environ.get("DATASHIELD_DATA_DIR"):
        d = Path(os.environ["DATASHIELD_DATA_DIR"])
    elif sys.platform == "win32":
        d = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        d = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME.lower()
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- defaults for model and feedback
MODEL_NAME = "deberta_v2_3_onnx"
SOURCE_ROOT = Path(__file__).resolve().parents[2]     # the folder holding datashield_local/ when run from source


def _in_source_checkout():
    return (SOURCE_ROOT / "train_ner.py").exists()


def default_model():
    """DATASHIELD_MODEL, else the source checkout's models/ (development), else the downloaded model
    in the data folder (`datashield-local download-model`)."""
    if os.environ.get("DATASHIELD_MODEL"):
        return Path(os.environ["DATASHIELD_MODEL"])
    local = SOURCE_ROOT / "models" / MODEL_NAME
    if _in_source_checkout() and local.exists():
        return local
    return data_dir() / "models" / MODEL_NAME


def default_feedback_db():
    """The source checkout keeps feedback/feedback.db (gitignored); installed copies use the data folder."""
    if _in_source_checkout():
        return SOURCE_ROOT / "feedback" / "feedback.db"
    return data_dir() / "feedback" / "feedback.db"
