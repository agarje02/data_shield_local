"""The review app is now served by the local engine (engine/), so this is a shortcut to it.

  python -m app.server                      same as: python -m engine
  python -m app.server --model models/deberta_v1_onnx
  then open http://127.0.0.1:8765
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datashield_local.engine.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
