"""`python -m engine` from a source checkout; same as `python -m datashield_local` / the `datashield-local` command."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datashield_local.engine.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
