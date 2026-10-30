"""Moved to datashield_local/checksums.py (the installable package). This alias keeps the training and data
scripts (`from checksums import ...`) working unchanged."""
import sys

from datashield_local import checksums as _module

sys.modules[__name__] = _module
