"""Backward-compatible import path: same module object as ``app.services.instamart.mcp_executor``."""

from __future__ import annotations

import sys
from importlib import import_module

_mod = import_module("app.services.instamart.mcp_executor")
sys.modules[__name__] = _mod
