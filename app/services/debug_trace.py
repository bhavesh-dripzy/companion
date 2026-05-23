"""Env-gated structured trace logger for the agent pipeline.

Used only when ``COMPANION_DEBUG_TRACE=1`` is set in the process env. Designed
to make every LLM/tool handoff observable so we can pinpoint where a
multi-turn flow loses state (cart_id, restaurant_id, etc.) or hallucinates a
reply. Production builds leave the env var unset and the trace is a no-op.

Output:
- Each record is one JSON line written to stderr AND to a log file
  (default: ``/tmp/companion-trace.log``, override with
  ``COMPANION_DEBUG_TRACE_FILE``).
- Fields are truncated so a huge MCP envelope doesn't blow up the log; raw
  base64 (e.g. QR PNG) is replaced with ``"<bytes len=N>"``.

Usage::

    from app.services.agents.debug_trace import trace, snip
    trace("orchestrator.in", message=user_message, history_turns=len(history))
    trace("planner.out",     plan=snip(plan))
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_ENV_FLAG = "COMPANION_DEBUG_TRACE"
_ENV_FILE = "COMPANION_DEBUG_TRACE_FILE"
_ENV_MAX_LEN = "COMPANION_DEBUG_TRACE_MAX_LEN"


def _is_enabled() -> bool:
    return os.getenv(_ENV_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


def _log_path() -> Path:
    return Path(os.getenv(_ENV_FILE, "/tmp/companion-trace.log"))


def _max_field_len() -> int:
    raw = os.getenv(_ENV_MAX_LEN, "4000").strip()
    try:
        n = int(raw)
        return max(200, n)
    except ValueError:
        return 4000


_lock = threading.Lock()
_turn_id_var: dict[int, str] = {}  # thread-id -> current turn id


def new_turn(turn_id: str | None = None) -> str:
    """Mark the start of a new agent turn. Returns the turn id so callers can echo it."""
    tid = turn_id or uuid.uuid4().hex[:8]
    _turn_id_var[threading.get_ident()] = tid
    return tid


def current_turn() -> str:
    return _turn_id_var.get(threading.get_ident(), "no-turn")


def snip(value: Any, max_len: int | None = None) -> Any:
    """Truncate strings / hide base64 blobs / shrink deep structures for logging.

    - Strings longer than ``max_len`` are cut and suffixed with ``…+N more``.
    - Dict keys named ``data_b64`` / ``qr_b64`` / ``code_verifier`` etc. are
      replaced with ``"<bytes len=N>"`` so the log stays readable.
    - Lists are kept whole (we already truncate menus elsewhere), but each
      element is recursed through ``snip``.
    """
    limit = max_len if isinstance(max_len, int) else _max_field_len()
    return _snip(value, limit)


_SENSITIVE_KEYS = frozenset(
    {
        "data_b64",
        "qr_b64",
        "code_verifier",
        "access_token",
        "refresh_token",
        "id_token",
        "Authorization",
        "authorization",
    }
)


def _snip(value: Any, limit: int) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[:limit] + f"…+{len(value) - limit} more"
    if isinstance(value, (bytes, bytearray)):
        return f"<bytes len={len(value)}>"
    if isinstance(value, list):
        return [_snip(v, limit) for v in value]
    if isinstance(value, tuple):
        return tuple(_snip(v, limit) for v in value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if key in _SENSITIVE_KEYS and isinstance(v, str):
                out[key] = f"<bytes len={len(v)}>"
            else:
                out[key] = _snip(v, limit)
        return out
    return repr(value)


def trace(stage: str, **fields: Any) -> None:
    """Emit a single trace record. No-op when the env flag is off."""
    if not _is_enabled():
        return
    record = {
        "ts": round(time.time(), 3),
        "turn": current_turn(),
        "stage": stage,
        **{k: _snip(v, _max_field_len()) for k, v in fields.items()},
    }
    try:
        line = json.dumps(record, ensure_ascii=False, default=str)
    except Exception as e:
        line = json.dumps({"ts": record["ts"], "stage": stage, "turn": record["turn"], "trace_error": str(e)})
    with _lock:
        try:
            sys.stderr.write(f"[TRACE] {line}\n")
            sys.stderr.flush()
        except Exception:
            pass
        try:
            path = _log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def reset_log_file() -> None:
    """Truncate the on-disk trace log so a replay starts from a clean slate."""
    if not _is_enabled():
        return
    try:
        _log_path().write_text("", encoding="utf-8")
    except Exception:
        pass
