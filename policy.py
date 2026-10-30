"""Moved to datashield_local/policy.py (the installable package). This alias keeps the training and data
scripts (`from policy import ...`) working unchanged."""
import sys

from datashield_local import policy as _module

sys.modules[__name__] = _module
