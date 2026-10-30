"""Moved to datashield_local/validators.py (the installable package). This alias keeps the training and data
scripts (`from validators import ...`) working unchanged."""
import sys

from datashield_local import validators as _module

sys.modules[__name__] = _module
