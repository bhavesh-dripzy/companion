"""Drive a RET10 BAP transaction flow against Pramaan's mock seller.

The BAP is the requester. ``start_order_flow`` sends ``search``; then as each
``on_*`` callback lands on the ``/ondc/{action}`` receiver, ``advance`` sends the
next action (on_search→select→init→confirm→status), carrying per-transaction
state in the txn store. The billing ``created_at`` is generated once at init and
reused at confirm so the two payloads stay byte-identical (a Pramaan check).
Targets Beckn ``core_version`` 1.2.0.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from companion_api.ondc_catalog import parse_catalog
from companion_api.ondc_client import send_action, send_catalog_rejection
from companion_api.ondc_retail import (
    cancel_message,
    catalog_rejection_errors,
    confirm_message,
    init_message,
    search_intent,
    select_message,
    status_message,
    track_message,
    update_return_message,
)
from companion_api.ondc_igm import send_issue
from companion_api.ondc_rsf import send_recon
from companion_api.ondc_store import get_transaction, save_transaction

logger = logging.getLogger(__name__)

DEFAULT_DOMAIN = "ONDC:RET10"

_EXPECTED_STEP = {
    "on_search": "search",
    "on_select": "select",
    "on_init": "init",
    "on_confirm": "confirm",
    "on_status": "status",
}


def _now_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def start_order_flow(
    query: str,
    *,
    gps: str | None = None,
    area_code: str | None = None,
    domain: str = DEFAULT_DOMAIN,
    category_id: str | None = None,
    terminal_step: str = "track",
    flow_mode: str = "order",
    search_type: str | None = None,
) -> dict[str, Any]:
    """Kick off a flow: send ``search`` to the gateway, persist state, return the ACK.

    ``terminal_step`` bounds the auto-chain (search->select->init->confirm->status->track);
    e.g. "confirm" stops after the order is placed so a follow-up action (cancel/update)
    can be driven via ``send_flow_action``. ``flow_mode`` controls what on_search does:
    "order" (default, select->...), "search_only" (stop after on_search — Flows 8A/B/C),
    "reject" (send catalog_rejection — Flow 9). ``search_type`` adds catalog tags
    (full / inc_pull / inc_push) for the discovery flows.
    """
    message = search_intent(query, gps=gps, area_code=area_code, category_id=category_id, search_type=search_type)
    result = send_action("search", message, domain=domain)
    txn = (result.get("context") or {}).get("transaction_id", "")
    save_transaction(
        txn, step="search", domain=domain, query=query, gps=gps, area_code=area_code,
        terminal_step=terminal_step, flow_mode=flow_mode,
    )
    logger.warning("ONDC flow started txn=%s query=%r mode=%s terminal=%s ack=%s", txn, query, flow_mode, terminal_step, result.get("status_code"))
    return {"transaction_id": txn, "search": result}


def advance(action: str, body: dict[str, Any]) -> dict[str, Any] | None:
    """Advance the flow on an inbound ``on_*`` callback. Returns the next action's send result."""
    context = body.get("context") or {}
    txn = context.get("transaction_id") or ""
    handler = {
        "on_search": _on_search,
        "on_select": _on_select,
        "on_init": _on_init,
        "on_confirm": _on_confirm,
        "on_status": _on_status,
    }.get(action)
    if not txn or not handler:
        return None
    state = get_transaction(txn)
    expected = _EXPECTED_STEP.get(action)
    if expected and state.get("step") != expected:
        logger.info("ONDC flow: ignoring %s (step=%s want=%s) txn=%s", action, state.get("step"), expected, txn)
        return None
    domain = state.get("domain") or context.get("domain") or DEFAULT_DOMAIN
    try:
        result = handler(txn, state, body, domain)
        if result:
            logger.warning("ONDC flow: %s -> sent %s status=%s txn=%s", action, result.get("url"), result.get("status_code"), txn)
        return result
    except Exception:
        logger.exception("ONDC flow advance failed action=%s txn=%s", action, txn)
        return None


def _bpp(state: dict[str, Any], body: dict[str, Any]) -> tuple[str | None, str | None]:
    context = body.get("context") or {}
    return state.get("bpp_id") or context.get("bpp_id"), state.get("bpp_uri") or context.get("bpp_uri")


def _on_search(txn: str, state: dict[str, Any], body: dict[str, Any], domain: str) -> dict[str, Any] | None:
    parsed = parse_catalog(body)
    bpp_id, bpp_uri = parsed.get("bpp_id"), parsed.get("bpp_uri")
    target = os.getenv("ONDC_TARGET_BPP", "").strip()
    if target and target not in (bpp_id or "") and target not in (bpp_uri or ""):
        logger.info("ONDC flow on_search: skipping non-target bpp_id=%s bpp_uri=%s txn=%s", bpp_id, bpp_uri, txn)
        return None
    mode = state.get("flow_mode", "order")
    if mode == "search_only":
        save_transaction(txn, **{**state, "step": "done", "bpp_id": bpp_id, "bpp_uri": bpp_uri})
        logger.warning("ONDC flow on_search: search_only done txn=%s", txn)
        return None
    if mode == "reject":
        save_transaction(txn, **{**state, "step": "catalog_rejection", "bpp_id": bpp_id, "bpp_uri": bpp_uri})
        return send_catalog_rejection(
            catalog_rejection_errors(), domain=domain, bpp_id=bpp_id, bpp_uri=bpp_uri, transaction_id=txn
        )
    provider = next((p for p in (parsed.get("providers") or []) if p.get("items")), None)
    if not provider:
        logger.warning("ONDC flow on_search: no provider with items txn=%s", txn)
        return None
    items = provider["items"]
    item_ids = [i["id"] for i in items[:1] if i.get("id")]
    fulfillment_id = next((i.get("fulfillment_id") for i in items if i.get("fulfillment_id")), None)
    location_id = next((i.get("location_id") for i in items if i.get("location_id")), None)
    location_ids = [location_id] if location_id else None
    save_transaction(
        txn,
        **{**state, "step": "select", "bpp_id": bpp_id, "bpp_uri": bpp_uri, "provider_id": provider["id"],
           "item_ids": item_ids, "fulfillment_id": fulfillment_id, "location_ids": location_ids},
    )
    message = select_message(
        provider["id"], item_ids, fulfillment_id=fulfillment_id, location_ids=location_ids,
        gps=state.get("gps"), area_code=state.get("area_code"),
    )
    return send_action("select", message, domain=domain, transaction_id=txn, bpp_id=bpp_id, bpp_uri=bpp_uri)


def _on_select(txn: str, state: dict[str, Any], body: dict[str, Any], domain: str) -> dict[str, Any] | None:
    bpp_id, bpp_uri = _bpp(state, body)
    if state.get("terminal_step") == "select":
        save_transaction(txn, **{**state, "step": "done", "bpp_id": bpp_id, "bpp_uri": bpp_uri})
        logger.warning("ONDC flow on_select: terminal=select (OOS/Flow 5), stopping txn=%s", txn)
        return None
    created_at = _now_iso()
    save_transaction(txn, **{**state, "step": "init", "bpp_id": bpp_id, "bpp_uri": bpp_uri, "billing_created_at": created_at})
    message = init_message(
        state["provider_id"], state["item_ids"], created_at=created_at, location_ids=state.get("location_ids"),
        fulfillment_id=state.get("fulfillment_id"), gps=state.get("gps"), area_code=state.get("area_code"),
    )
    return send_action("init", message, domain=domain, transaction_id=txn, bpp_id=bpp_id, bpp_uri=bpp_uri)


def _on_init(txn: str, state: dict[str, Any], body: dict[str, Any], domain: str) -> dict[str, Any] | None:
    bpp_id, bpp_uri = _bpp(state, body)
    on_init_order = (body.get("message") or {}).get("order") or {}
    created_at = state.get("billing_created_at") or _now_iso()
    message = confirm_message(on_init_order, created_at=created_at)
    order_id = message["order"]["id"]
    save_transaction(txn, **{**state, "step": "confirm", "bpp_id": bpp_id, "bpp_uri": bpp_uri, "order_id": order_id})
    return send_action("confirm", message, domain=domain, transaction_id=txn, bpp_id=bpp_id, bpp_uri=bpp_uri)


def _on_confirm(txn: str, state: dict[str, Any], body: dict[str, Any], domain: str) -> dict[str, Any] | None:
    bpp_id, bpp_uri = _bpp(state, body)
    order = (body.get("message") or {}).get("order") or {}
    order_id = order.get("id") or state.get("order_id") or ""
    if state.get("terminal_step") == "confirm" or not order_id:
        save_transaction(txn, **{**state, "step": "done", "order_id": order_id})
        return None
    save_transaction(txn, **{**state, "step": "status", "order_id": order_id})
    return send_action(
        "status", status_message(order_id), domain=domain, transaction_id=txn, bpp_id=bpp_id, bpp_uri=bpp_uri
    )


def _on_status(txn: str, state: dict[str, Any], body: dict[str, Any], domain: str) -> dict[str, Any] | None:
    bpp_id, bpp_uri = _bpp(state, body)
    order = (body.get("message") or {}).get("order") or {}
    order_id = order.get("id") or state.get("order_id") or ""
    if state.get("terminal_step") == "status" or not order_id:
        save_transaction(txn, **{**state, "step": "done", "order_id": order_id})
        return None
    save_transaction(txn, **{**state, "step": "track", "order_id": order_id})
    return send_action(
        "track", track_message(order_id), domain=domain, transaction_id=txn, bpp_id=bpp_id, bpp_uri=bpp_uri
    )


_ACTION_MESSAGE = {
    "cancel": lambda state, params: cancel_message(state.get("order_id", ""), params.get("reason_id", "001")),
    "status": lambda state, params: status_message(state.get("order_id", "")),
    "track": lambda state, params: track_message(state.get("order_id", "")),
    "update": lambda state, params: update_return_message(
        state.get("order_id", ""), state.get("item_ids"),
        reason_id=params.get("reason_id", "001"), quantity=params.get("quantity", "1"),
    ),
}


def send_flow_action(txn: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Send a follow-up buyer action (cancel/status/track) for an existing txn, using stored state."""
    state = get_transaction(txn)
    if not state.get("order_id"):
        return {"error": "no order_id for transaction", "transaction_id": txn}
    if action == "recon":
        result = send_recon(
            bpp_id=state.get("bpp_id", ""), bpp_uri=state.get("bpp_uri", ""),
            order_id=state.get("order_id", ""), transaction_id=txn,
        )
        save_transaction(txn, **{**state, "step": "recon"})
        return {"transaction_id": txn, "action": action, "result": result}
    if action in ("issue", "issue_close"):
        result = send_issue(txn, state, {**params, "close": action == "issue_close" or params.get("close", False)})
        save_transaction(txn, **{**state, "step": action})
        return {"transaction_id": txn, "action": action, "result": result}
    builder = _ACTION_MESSAGE.get(action)
    if not builder:
        return {"error": f"unsupported action {action}", "transaction_id": txn}
    bpp_id, bpp_uri = state.get("bpp_id"), state.get("bpp_uri")
    domain = state.get("domain") or DEFAULT_DOMAIN
    result = send_action(
        action, builder(state, params), domain=domain, transaction_id=txn, bpp_id=bpp_id, bpp_uri=bpp_uri
    )
    save_transaction(txn, **{**state, "step": action})
    logger.warning("ONDC flow action %s -> sent %s status=%s txn=%s", action, result.get("url"), result.get("status_code"), txn)
    return {"transaction_id": txn, "action": action, "result": result}
