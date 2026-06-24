"""Agent flow debug logger. Enable with env AGENT_DEBUG=1.

Emits one line per hop (turn → router → provider-select → resolver → cart strategy →
MCP → response) so the whole pipeline is observable without a debugger. Off by default
(no overhead) — `log.debug(...)` is a no-op unless AGENT_DEBUG is set.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("companion.agent")

if os.getenv("AGENT_DEBUG", "").strip().lower() in ("1", "true", "yes", "on"):
    log.setLevel(logging.DEBUG)
    if not log.handlers:
        _h = logging.StreamHandler()
        _h.setFormatter(logging.Formatter("AGENT ▸ %(message)s"))
        log.addHandler(_h)
        log.propagate = False


def short(value, n: int = 160) -> str:
    s = str(value)
    return s if len(s) <= n else s[:n] + "…"
