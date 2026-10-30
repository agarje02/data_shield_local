"""Moved to datashield_local/common.py (the installable package). This alias keeps the training and data
scripts (`from common import ...`) working unchanged."""
import sys

from datashield_local import common as _module

sys.modules[__name__] = _module
