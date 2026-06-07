"""Backward-compatible import path for ``twilio_mcp_executor`` references.

This shim allows code that imports ``app.services.twilio_mcp_executor`` to
resolve to the same module object as ``app.services.twilio.executor``.
"""

from __future__ import annotations

import sys
from importlib import import_module

_mod = import_module("app.services.twilio.executor")
sys.modules[__name__] = _mod
