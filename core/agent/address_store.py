"""The agent's view of the per-user, per-provider default delivery address — the ONE global source of
truth, shared with the Apps/settings modal.

It does NOT keep its own copy: it delegates to the canonical per-provider stores in companion_api
(`get/save_default_<provider>_address`), which persist to DynamoDB (ProfileCacheRepository) with a
local-file dev fallback and a short TTL cache. So a switch from chat and a change from the Apps modal
write the SAME record — change it anywhere, it's reflected everywhere (cart, search, settings, every
container). The import is lazy (companion_api → core would otherwise invert layering), mirroring
core/llm/config.py.
"""

from __future__ import annotations

import contextlib
from typing import Any

_PROVIDERS = ("instamart", "zepto")  # grocery apps with a saved default address


def _fns(provider: str):
    """(getter, saver) for a provider's canonical default-address store, or (None, None)."""
    if provider not in _PROVIDERS:
        return None, None
    try:
        import importlib

        mod = importlib.import_module(f"companion_api.{provider}_address")
        return (
            getattr(mod, f"get_default_{provider}_address"),
            getattr(mod, f"save_default_{provider}_address"),
        )
    except Exception:
        return None, None


def _split_label(label: str) -> tuple[str | None, str | None]:
    """A combined picker label ('tag · line' / 'tag — line') → (tag, line) for the canonical record."""
    for sep in (" · ", " — "):
        if sep in label:
            tag, line = label.split(sep, 1)
            return tag.strip() or None, line.strip() or None
    label = label.strip()
    return (label or None), None


def get_one(user_id: str | None, provider: str) -> dict[str, Any] | None:
    """The user's selected address for one provider as {address_id, label}, or None."""
    getter, _ = _fns(provider)
    if getter is None or not user_id:
        return None
    rec = getter(user_id)
    if not rec or not rec.get("address_id"):
        return None
    label = " · ".join(p for p in (rec.get("tag"), rec.get("line")) if p) or None
    return {"address_id": rec["address_id"], "label": label}


def get(user_id: str | None) -> dict[str, dict]:
    """Every provider's selected address: {provider: {address_id, label}} (only those set)."""
    out: dict[str, dict] = {}
    for pid in _PROVIDERS:
        blob = get_one(user_id, pid)
        if blob:
            out[pid] = blob
    return out


def set(user_id: str | None, provider: str, blob: dict) -> None:
    """Persist a provider's selected address to the canonical global store. `blob` = {address_id, label?}."""
    _, saver = _fns(provider)
    aid = (blob or {}).get("address_id")
    if saver is None or not user_id or not aid:
        return
    tag, line = _split_label((blob.get("label") or "").strip())
    # Never let a persistence hiccup break the turn (the in-session pin still holds).
    with contextlib.suppress(Exception):
        saver(user_id, address_id=str(aid), tag=tag, line=line)
