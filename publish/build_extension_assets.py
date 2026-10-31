"""Copy what the extension needs from the Python side into extension/engine/.

  python publish/build_extension_assets.py

  policy.json   datashield_local/policy.yaml as JSON (the extension's default policy)

Run after editing policy.yaml; tests/test_extension_assets.py fails while the copies differ.
"""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
POLICY_YAML = ROOT / "datashield_local" / "policy.yaml"
POLICY_JSON = ROOT / "extension" / "engine" / "policy.json"


def policy_json():
    return json.dumps(yaml.safe_load(POLICY_YAML.read_text(encoding="utf-8")), indent=1) + "\n"


def main():
    POLICY_JSON.write_text(policy_json(), encoding="utf-8")
    print(f"wrote {POLICY_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
