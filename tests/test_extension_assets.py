import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_assets", ROOT / "publish" / "build_extension_assets.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def test_extension_policy_matches_policy_yaml():
    # stale copy: run python publish/build_extension_assets.py
    assert build.POLICY_JSON.read_text(encoding="utf-8") == build.policy_json()
