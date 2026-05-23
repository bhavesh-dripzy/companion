"""Agent entry: orchestrator → domain food planner → MCP → response LLM."""

from __future__ import annotations

from typing import Any

from app.services.agents.orchestrator import classify_agent_routing
from app.services.agents.tool_trace import build_tool_execution_trace
from app.services.debug_trace import new_turn, snip, trace
from app.services.food_llm_router import route_food_query
from app.services.food_response_builder import (
    build_assistant_message_with_llm,
    build_zomato_summary,
)
from app.services.calendar import (
    execute_reminder_calendar,
    plan_reminder_calendar,
    reply_reminder_calendar,
)
from app.services.general_chat import plan_normal_query
from app.services.general_chat import responder as general_chat_responder
from app.services.llm_config import ResolvedLlm
from app.services.maps import (
    execute_maps_query,
    plan_maps_query,
    reply_maps_query,
)
from app.services.tool_runtime import execute_tool_plan_sync, get_tool_provider


class CustomMcpNeedsDiscoveryError(ValueError):
    """Raised when a routed custom MCP lacks a cached tool manifest."""


# Largest single attachment we'll surface to the chat UI in one turn. The
# Zomato QR PNG is ~5 KB, but inline base64 images grow as 1.33x the raw
# byte count and bloat history payloads; cap conservatively so a runaway
# provider can't tank the chat bubble.
_MAX_ATTACHMENT_DATA_B64_CHARS = 200_000  # ~150 KB raw


def _collect_payment_attachments(raw_tool_results: dict[str, Any]) -> list[dict[str, Any]]:
    """Lift any ``payment.qr_b64`` blocks out of raw tool steps.

    Walks every provider's executor output (currently only Zomato emits a
    ``payment`` block, but the contract is generic) and returns a flat list of
    attachment descriptors the frontend can render inline. The original step
    output is left untouched so downstream consumers (response-LLM, observable
    trace, debug payloads) see the same structure.

    Attachment shape (stable contract for ``out["attachments"]``)::

        {
          "kind": "image",
          "source": "zomato.checkout_cart" | "<provider>.<tool>",
          "mime_type": "image/png",
          "data_b64": "<base64 payload>",
          "alt": "Payment QR code",
          "caption": "Scan with any UPI app",
          # optional, only when the QR was decoded:
          "upi_link": "upi://pay?pa=...",
          "amount_inr": "185",
          "payee_vpa": "...",
          "payee_name": "...",
          "transaction_ref": "...",
        }
    """
    if not isinstance(raw_tool_results, dict):
        return []

    attachments: list[dict[str, Any]] = []
    for provider_name, raw in raw_tool_results.items():
        if not isinstance(raw, dict):
            continue
        steps = raw.get("steps") or []
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            envelope = step.get("result")
            if not isinstance(envelope, dict):
                continue
            payment = envelope.get("payment")
            if not isinstance(payment, dict):
                continue
            data_b64 = payment.get("qr_b64")
            if not isinstance(data_b64, str) or not data_b64.strip():
                continue
            if len(data_b64) > _MAX_ATTACHMENT_DATA_B64_CHARS:
                # Refuse to inline absurdly large payloads — drop a breadcrumb
                # in the attachment so the UI can fall back to a link.
                attachment_oversize = {
                    "kind": "image",
                    "source": f"{provider_name}.{step.get('tool') or 'unknown'}",
                    "mime_type": payment.get("qr_mime_type") or "image/png",
                    "data_b64": None,
                    "oversize": True,
                    "alt": "Payment QR code",
                    "caption": "QR image too large to inline; open via UPI link.",
                    "upi_link": payment.get("upi_link"),
                    "amount_inr": payment.get("amount_inr"),
                    "payee_vpa": payment.get("payee_vpa"),
                    "payee_name": payment.get("payee_name"),
                    "transaction_ref": payment.get("transaction_ref"),
                }
                attachments.append(
                    {k: v for k, v in attachment_oversize.items() if v is not None}
                )
                continue

            amount = payment.get("amount_inr")
            payee = payment.get("payee_name") or payment.get("payee_vpa")
            caption_parts = ["Scan with any UPI app"]
            if amount:
                caption_parts.append(f"₹{amount}")
            if payee:
                caption_parts.append(f"to {payee}")
            caption = " — ".join(caption_parts)

            attachment: dict[str, Any] = {
                "kind": "image",
                "source": f"{provider_name}.{step.get('tool') or 'checkout_cart'}",
                "mime_type": payment.get("qr_mime_type") or "image/png",
                "data_b64": data_b64,
                "alt": "Payment QR code",
                "caption": caption,
            }
            for key in ("upi_link", "amount_inr", "payee_vpa", "payee_name", "transaction_ref"):
                val = payment.get(key)
                if val:
                    attachment[key] = val
            attachments.append(attachment)
    return attachments


def _format_routing_message(
    current_message: str,
    conversation_history: list[dict[str, str]] | None = None,
    *,
    session_state_keys: list[str] | None = None,
    session_restaurants: list[dict[str, Any]] | None = None,
) -> str:
    """Build planner/orchestrator input: recent transcript + current user turn.

    When ``session_state_keys`` is non-empty, append a compact line listing the
    placeholder names the executor already remembers from prior turns (e.g.
    ``__CART_ID__``, ``__RESTAURANT_ID__``). Values are intentionally **not**
    included — the planner only needs to know *what* state exists so it can
    decide whether to re-run a tool or rely on placeholder substitution. The
    executor still owns the actual ID values.

    When ``session_restaurants`` is non-empty, append a small table of
    (name, res_id) pairs the user has already been shown. The planner can use
    these literal res_ids in tool arguments (e.g. ``get_menu_items_listing``
    or ``create_cart``) to skip a redundant — and often unreliable, since
    Zomato's keyword search is fuzzy — re-search.
    """
    current = (current_message or "").strip()
    parts: list[str] = []
    if conversation_history:
        recent = [
            item
            for item in conversation_history[-8:]
            if isinstance(item, dict)
            and item.get("role") in {"user", "assistant"}
            and item.get("content")
        ]
        if recent:
            transcript = "\n".join(
                f"{item.get('role')}: {str(item.get('content') or '')[:700]}"
                for item in recent
            )
            parts.append(f"Conversation so far:\n{transcript}")
    if session_state_keys:
        keys = sorted({str(k) for k in session_state_keys if isinstance(k, str) and k.strip()})
        if keys:
            parts.append(
                "Carried-forward placeholders available (set by previous turns; "
                "use them directly in tool arguments — the executor will substitute "
                "the real values): " + ", ".join(keys)
            )
    if session_restaurants:
        # Cap how many we surface to the planner — too long bloats every prompt.
        # 10 is the same order of magnitude as a typical user-visible result list.
        rows: list[str] = []
        for entry in session_restaurants[:10]:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            rid = str(entry.get("res_id") or "").strip()
            if not name or not rid:
                continue
            rating = entry.get("rating")
            tail = f" (rating {rating})" if rating not in (None, "") else ""
            rows.append(f"  - {name} → res_id={rid}{tail}")
        if rows:
            parts.append(
                "Restaurants already shown to the user this session — if the "
                "user picks one of these by name, use the literal res_id below "
                "instead of running get_restaurants_for_keyword again "
                "(Zomato's keyword search is fuzzy and often returns the wrong "
                "restaurant for short brand names):\n" + "\n".join(rows)
            )
    parts.append(f"Current user message:\n{current}")
    return "\n\n".join(parts) if len(parts) > 1 else current


_KNOWN_PROVIDER_IDS = frozenset(
    {"zomato", "swiggy_food", "swiggy_instamart", "zepto", "kite", "groww", "daily_calorie_tracker"}
)


def _extract_previous_provider(
    conversation_history: list[dict[str, str]] | None,
) -> str | None:
    """Best-effort provider stickiness signal.

    Priority:
      1. Explicit ``provider`` field on the most recent assistant history item
         (forward-compatible with frontends that echo back routing info).
      2. Heuristic scan of the last few assistant turns for unambiguous
         provider markers in the message text. Prefers the *latest* non-empty
         signal to avoid drifting back to a stale provider.

    Returns ``None`` when nothing reliable is found — callers must treat that
    as "no hint" rather than as a default.
    """
    if not conversation_history:
        return None
    recent = [
        item
        for item in conversation_history[-6:]
        if isinstance(item, dict) and item.get("role") == "assistant"
    ]
    for item in reversed(recent):
        pid = item.get("provider")
        if isinstance(pid, str) and pid.strip() in _KNOWN_PROVIDER_IDS:
            return pid.strip()
    for item in reversed(recent):
        content = str(item.get("content") or "").lower()
        if not content:
            continue
        has_instamart = "instamart" in content
        has_zepto = "zepto" in content
        has_zomato = "zomato" in content
        has_swiggy = "swiggy" in content and not has_instamart
        if has_instamart:
            return "swiggy_instamart"
        if has_zepto:
            return "zepto"
        if has_zomato:
            return "zomato"
        if has_swiggy:
            return "swiggy_food"
    return None


def _ensure_custom_providers_ready(routed_providers: set[str], user_id: str) -> None:
    for pid in sorted(routed_providers):
        tp = get_tool_provider(pid, user_id=user_id)
        if tp and tp.custom and not tp.tools:
            raise CustomMcpNeedsDiscoveryError(
                "Test this custom MCP first so Companion can discover its tools."
            )


def _empty_constraints_template() -> dict[str, Any]:
    return {
        "requires_address_resolution": False,
        "requested_location_text": None,
        "inferred_keyword": None,
        "price_max_inr": None,
        "price_min_inr": None,
        "min_rating": None,
        "near_and_fast": None,
        "vegetarian_only": None,
        "party_size_estimate": None,
        "health_constraints": None,
    }


def _non_food_plan(*, reasoning: str) -> dict[str, Any]:
    return {
        "intent": "unsupported_task",
        "summary": reasoning,
        "tool_plan": [],
        "constraints": _empty_constraints_template(),
        "execution_notes_for_downstream": "Task type is not wired to MCP tools yet.",
        "validation": {
            "tool_count": 0,
            "dropped_unknown_tool_steps": 0,
            "resolved_model": "orchestrator",
            "completion_id": "",
            "provider": "system",
        },
    }


def _empty_food_plan(*, reasoning: str) -> dict[str, Any]:
    return {
        "intent": "clarify_request",
        "summary": reasoning,
        "tool_plan": [],
        "constraints": _empty_constraints_template(),
        "execution_notes_for_downstream": "No food MCP providers selected; respond conversationally.",
        "validation": {
            "tool_count": 0,
            "dropped_unknown_tool_steps": 0,
            "resolved_model": "orchestrator",
            "completion_id": "",
            "provider": "system",
        },
    }


def _empty_provider_plan(*, reasoning: str, task_type: str) -> dict[str, Any]:
    return {
        "intent": "connect_or_discover_provider",
        "summary": reasoning,
        "tool_plan": [],
        "constraints": {},
        "execution_notes_for_downstream": (
            f"{task_type} provider has no cached tool schema yet."
        ),
        "validation": {
            "tool_count": 0,
            "dropped_unknown_tool_steps": 0,
            "resolved_model": "orchestrator",
            "completion_id": "",
            "provider": "system",
        },
    }


def _merge_provider_food_plans(
    labeled: list[tuple[str, dict[str, Any]]],
    *,
    resolved_model: str,
    routing_provider: str,
) -> dict[str, Any]:
    merged_steps: list[dict[str, Any]] = []
    summaries: list[str] = []
    intents: list[str] = []
    merged_constraints: dict[str, Any] = {}

    for pid, partial in labeled:
        st = str(partial.get("summary") or "").strip()
        if st:
            summaries.append(st)
        it = str(partial.get("intent") or "").strip()
        if it:
            intents.append(it)
        raw_c = partial.get("constraints")
        if isinstance(raw_c, dict):
            for key, val in raw_c.items():
                if key not in merged_constraints:
                    merged_constraints[key] = val
                elif key == "requires_address_resolution":
                    merged_constraints[key] = bool(merged_constraints.get(key)) or bool(val)
                elif merged_constraints.get(key) in (None, "", []) and val not in (None, "", []):
                    merged_constraints[key] = val

        tpl = partial.get("tool_plan")
        if not isinstance(tpl, list):
            continue
        for step in tpl:
            if not isinstance(step, dict):
                continue
            step_out = dict(step)
            if not str(step_out.get("provider") or "").strip():
                step_out["provider"] = pid
            merged_steps.append(step_out)

    if not merged_constraints:
        merged_constraints = _empty_constraints_template()

    summary_joined = " · ".join(summaries) if summaries else "Coordinated requests across providers."
    unique_intents = {i for i in intents if i}
    intent_pick = (
        next(iter(unique_intents)) if len(unique_intents) == 1 else "multi_provider_food_request"
    )

    return {
        "intent": intent_pick,
        "summary": summary_joined,
        "tool_plan": merged_steps,
        "constraints": merged_constraints,
        "execution_notes_for_downstream": (
            "Merged independent per-provider MCP plans; execute in parallel when possible."
        ),
        "validation": {
            "tool_count": len(merged_steps),
            "dropped_unknown_tool_steps": 0,
            "resolved_model": resolved_model,
            "completion_id": "",
            "provider": routing_provider,
        },
    }


def _plan_for_provider(
    provider_id: str,
    *,
    routing_message: str,
    llm: ResolvedLlm,
    user_id: str,
) -> dict[str, Any]:
    """Delegate to the provider's domain agent (LLM + prompt → tool_plan)."""
    tp = get_tool_provider(provider_id, user_id=user_id)
    trace("planner.in", provider=provider_id, routing_message=routing_message)

    if provider_id == "swiggy_food":
        from app.services.swiggy.planner import plan_swiggy_food

        out = plan_swiggy_food(user_message=routing_message, llm=llm)
        trace("planner.out", provider=provider_id, plan=snip(out))
        return out
    if provider_id == "swiggy_instamart":
        from app.services.instamart.planner import plan_instamart

        out = plan_instamart(user_message=routing_message, llm=llm)
        trace("planner.out", provider=provider_id, plan=snip(out))
        return out
    if provider_id == "zomato":
        from app.services.zomato.planner import plan_zomato_food

        out = plan_zomato_food(user_message=routing_message, llm=llm)
        trace("planner.out", provider=provider_id, plan=snip(out))
        return out

    if tp and not tp.tools:
        return _empty_provider_plan(
            reasoning=(
                f"{tp.label} needs account authorization and tool discovery before "
                "Companion can plan calls for it. Use MCP Connect/Test first."
            ),
            task_type="finance_investing",
        )

    return route_food_query(
        user_message=routing_message,
        api_key=llm.api_key,
        model=llm.model,
        provider=llm.provider,
        base_url=llm.base_url,
        tool_provider=provider_id,
        tool_provider_label=tp.label if tp else None,
        tool_provider_tools=(sorted(tp.tools) if tp and tp.tools else None),
    )


def _resolve_food_routing_plan(
    *,
    routing_message: str,
    routed_providers: set[str],
    llm: ResolvedLlm,
    user_id: str,
) -> dict[str, Any]:
    if not routed_providers:
        return _empty_food_plan(
            reasoning="No food MCP providers remain after orchestration.",
        )

    if len(routed_providers) == 1:
        pid = next(iter(routed_providers))
        return _plan_for_provider(
            pid,
            routing_message=routing_message,
            llm=llm,
            user_id=user_id,
        )

    labeled: list[tuple[str, dict[str, Any]]] = []
    for pid in sorted(routed_providers):
        labeled.append(
            (
                pid,
                _plan_for_provider(
                    pid,
                    routing_message=routing_message,
                    llm=llm,
                    user_id=user_id,
                ),
            )
        )

    return _merge_provider_food_plans(
        labeled,
        resolved_model=llm.model,
        routing_provider=llm.provider,
    )


def run_agent_turn(
    *,
    user_message: str,
    address_id: str,
    execute_tools: bool,
    user_id: str,
    llm: ResolvedLlm,
    conversation_history: list[dict[str, str]] | None = None,
    user_geo: dict[str, Any] | None = None,
    session_state: dict[str, str] | None = None,
    session_restaurants: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Orchestrated agent turn producing the same response envelope as legacy /api/food/query/.

    Flow: orchestrator LLM → per-provider domain LLM (tool_plan) → MCP executor → response LLM.

    ``session_state`` carries forward IDs the executor learned on previous turns
    (cart_id, restaurant_id, order_id, …). Frontends should echo back the
    ``session_state`` returned from the previous turn's response. This is the
    mechanism that allows a Zomato cart created in turn N to be checked out
    in turn N+1 without the LLM ever having to see raw IDs.

    ``session_restaurants`` is the LRU-style list of every (name, res_id) pair
    the user has been shown across the session. It lets the planner pick a
    restaurant the user has seen *without* re-running Zomato's unreliable
    fuzzy keyword search. Like ``session_state``, frontends should echo back
    whatever the previous turn returned.
    """
    new_turn()
    # Normalize the restaurants seed once so we can both echo it back and
    # surface it in the planner prompt below.
    seed_restaurants: list[dict[str, Any]] = []
    if isinstance(session_restaurants, list):
        for entry in session_restaurants:
            if not isinstance(entry, dict):
                continue
            rid = entry.get("res_id")
            name = entry.get("name")
            if not isinstance(rid, str) or not rid.strip():
                continue
            if not isinstance(name, str) or not name.strip():
                continue
            seed_restaurants.append({**entry, "res_id": rid.strip(), "name": name.strip()})
    trace(
        "pipeline.in",
        user_id=user_id,
        user_message=user_message,
        address_id=address_id,
        history_turns=len(conversation_history or []),
        execute_tools=execute_tools,
        session_state_keys=sorted((session_state or {}).keys()),
    )
    # Orchestrator sees ONLY the transcript — it classifies task_type and
    # provider, neither of which should depend on stored placeholders.
    routing_message = _format_routing_message(user_message, conversation_history)
    # Planner needs the placeholder hint AND the recent-restaurants index so
    # it can (a) skip a redundant restaurant search when the user picks a
    # known restaurant and (b) avoid re-running upstream tools when downstream
    # state is already known.
    planner_routing_message = _format_routing_message(
        user_message,
        conversation_history,
        session_state_keys=sorted((session_state or {}).keys()) or None,
        session_restaurants=seed_restaurants or None,
    )
    previous_provider = _extract_previous_provider(conversation_history)
    trace(
        "orchestrator.in",
        routing_message=routing_message,
        previous_provider=previous_provider,
    )
    orchestration = classify_agent_routing(
        user_message=routing_message,
        llm=llm,
        previous_provider=previous_provider,
    )
    trace("orchestrator.out", orchestration=snip(orchestration))

    task_type = str(orchestration.get("task_type") or "food_delivery")
    if task_type == "finance_investing":
        routed = set(orchestration.get("finance_providers") or [])
    elif task_type == "health_nutrition":
        routed = set(orchestration.get("health_providers") or [])
    else:
        routed = set(orchestration.get("food_providers") or [])
    routed = {
        pid
        for pid in routed
        if isinstance(pid, str) and pid.strip() and pid.strip() != "blinkit"
    }

    if task_type == "whatsapp_message":
        from app.services.whatsapp.planner import plan_whatsapp_message
        from app.services.whatsapp.client import WhatsAppClient
        
        plan = plan_whatsapp_message(user_message=routing_message, user_id=user_id, llm=llm)
        whatsapp_result = None
        if execute_tools and plan.get("intent") == "send_whatsapp":
            client = WhatsAppClient()
            whatsapp_result = client.send_text_message(
                to=plan["recipient_phone"],
                text=plan["message"]
            )
        
        # Build a response message
        if plan.get("intent") == "send_whatsapp":
            if whatsapp_result and "error" not in whatsapp_result:
                assistant_message = f"Sent WhatsApp to {plan['recipient_name']}: \"{plan['message']}\""
            elif execute_tools:
                assistant_message = f"Failed to send WhatsApp to {plan['recipient_name']}. Please check credentials."
            else:
                assistant_message = f"I'll send a WhatsApp to {plan['recipient_name']} with the message: \"{plan['message']}\". (Execution skipped)"
        else:
            assistant_message = plan.get("reasoning") or "I couldn't figure out who to send that WhatsApp message to."

        return {
            "status": "success",
            "provider": llm.provider,
            "intent_routing": orchestration,
            "whatsapp_plan": plan,
            "whatsapp_result": whatsapp_result,
            "assistant_message": assistant_message,
        }

    if task_type == "normal_query":
        routed = set()
        plan = plan_normal_query(reasoning=str(orchestration.get("reasoning") or ""))
    elif task_type == "reminder_calendar":
        routed = set()
        plan = plan_reminder_calendar(
            reasoning=str(orchestration.get("reasoning") or ""),
            user_message=user_message,
        )
    elif task_type == "maps_query":
        routed = set()
        plan = plan_maps_query(
            reasoning=str(orchestration.get("reasoning") or ""),
            user_message=user_message,
            llm=llm,
        )
    elif task_type in {"finance_investing", "health_nutrition"}:
        if not routed:
            plan = _empty_provider_plan(
                reasoning=str(orchestration.get("reasoning") or "No MCP provider selected."),
                task_type=task_type,
            )
        else:
            _ensure_custom_providers_ready(routed, user_id)
            plan = _resolve_food_routing_plan(
                routing_message=planner_routing_message,
                routed_providers=routed,
                llm=llm,
                user_id=user_id,
            )
    elif task_type != "food_delivery":
        plan = _non_food_plan(reasoning=str(orchestration.get("reasoning") or task_type))
    elif not routed:
        plan = _empty_food_plan(reasoning=str(orchestration.get("reasoning") or ""))
    else:
        _ensure_custom_providers_ready(routed, user_id)
        plan = _resolve_food_routing_plan(
            routing_message=planner_routing_message,
            routed_providers=routed,
            llm=llm,
            user_id=user_id,
        )

    tool_execution: dict[str, Any] | None = None
    calendar_execution: dict[str, Any] | None = None
    maps_execution: dict[str, Any] | None = None
    provider_summaries: dict[str, Any] = {}
    zomato_summary = build_zomato_summary(None)
    raw_tool_results: dict[str, Any] = {}

    if task_type == "reminder_calendar" and execute_tools:
        calendar_execution = execute_reminder_calendar(
            user_message=user_message,
            user_id=user_id,
        )
    elif task_type == "maps_query" and execute_tools:
        maps_execution = execute_maps_query(
            plan=plan,
            user_geo=user_geo,
        )
    elif execute_tools:
        tool_plan = plan.get("tool_plan") if isinstance(plan, dict) else []
        enabled_providers = routed or None
        trace(
            "executor.in",
            providers=sorted(enabled_providers or []),
            tool_plan=snip(tool_plan),
            address_id=address_id,
            constraints=snip(plan.get("constraints") if isinstance(plan, dict) else {}),
        )
        tool_execution = execute_tool_plan_sync(
            tool_plan if isinstance(tool_plan, list) else [],
            address_id=address_id,
            constraints=plan.get("constraints") if isinstance(plan, dict) else {},
            user_message=user_message,
            enabled_providers=enabled_providers,
            user_id=user_id,
            session_placeholders=session_state,
            session_restaurants=seed_restaurants or None,
        )
        provider_results = tool_execution.get("provider_results")
        if isinstance(provider_results, dict):
            for provider_name, raw in provider_results.items():
                if isinstance(raw, dict):
                    raw_tool_results[provider_name] = raw
                    provider_summaries[provider_name] = build_zomato_summary(raw)
            zraw = provider_results.get("zomato")
            zomato_summary = build_zomato_summary(zraw if isinstance(zraw, dict) else None)
        trace(
            "executor.out",
            ok=tool_execution.get("ok"),
            error=tool_execution.get("error"),
            steps_by_provider={
                p: len((r or {}).get("steps") or [])
                for p, r in (provider_results or {}).items()
            },
            diagnostics_by_provider={
                p: snip((r or {}).get("diagnostics") or {})
                for p, r in (provider_results or {}).items()
            },
        )

    domain_execution: dict[str, Any] | None = (
        calendar_execution
        if task_type == "reminder_calendar"
        else maps_execution
        if task_type == "maps_query"
        else None
    )

    # Hoist any inline image attachments (currently only the Zomato checkout
    # payment QR) so the chat UI can render them next to the assistant bubble.
    # The base64 lives buried under `raw_tool_results.<provider>.steps[].result.payment`;
    # the LLM-written `assistant_message` cannot embed an image (the frontend's
    # markdown renderer doesn't accept image syntax, and the response-LLM is
    # told never to paste raw data), so we surface attachments as a separate
    # top-level field with a stable schema.
    attachments = _collect_payment_attachments(raw_tool_results)
    trace(
        "hoister.out",
        attachment_count=len(attachments),
        attachments=snip(attachments),
    )

    # Build the outgoing session_state. Start from whatever the caller sent in
    # (so IDs that weren't touched this turn carry forward — e.g. a restaurant
    # id from turn 1 still applies in turn 4) and overlay the values the
    # executor learned this turn. Executor wins for any collision because it
    # just observed the live MCP state.
    next_session_state: dict[str, str] = {}
    if isinstance(session_state, dict):
        for k, v in session_state.items():
            if isinstance(k, str) and isinstance(v, (str, int)) and str(v).strip():
                next_session_state[k] = str(v).strip()
    # Start the next-turn restaurant index from the seed; the executor's
    # diagnostics will override it if the current turn ran a search.
    next_session_restaurants: list[dict[str, Any]] = list(seed_restaurants)
    for _provider_name, _raw in (raw_tool_results or {}).items():
        if not isinstance(_raw, dict):
            continue
        _diag = _raw.get("diagnostics")
        if not isinstance(_diag, dict):
            continue
        _learned = _diag.get("session_placeholders")
        if isinstance(_learned, dict):
            for k, v in _learned.items():
                if isinstance(k, str) and isinstance(v, (str, int)) and str(v).strip():
                    next_session_state[k] = str(v).strip()
        _restos = _diag.get("session_restaurants")
        if isinstance(_restos, list) and _restos:
            # Executor already merged seed + new entries, so its list is the
            # authoritative next state. Overwriting (not appending) is what
            # keeps the LRU ordering meaningful — most-recent at the front.
            cleaned: list[dict[str, Any]] = []
            for entry in _restos:
                if not isinstance(entry, dict):
                    continue
                rid = entry.get("res_id")
                name = entry.get("name")
                if isinstance(rid, str) and rid.strip() and isinstance(name, str) and name.strip():
                    cleaned.append({**entry, "res_id": rid.strip(), "name": name.strip()})
            if cleaned:
                next_session_restaurants = cleaned
    trace(
        "session_state.out",
        in_keys=sorted((session_state or {}).keys()),
        out_keys=sorted(next_session_state.keys()),
        # Mask values: the keys are enough to debug stickiness, values can
        # contain customer-identifying IDs we don't want in trace logs.
        delta_keys=sorted(
            set(next_session_state.keys()) - set((session_state or {}).keys())
        ),
    )

    out: dict[str, Any] = {
        "status": "success",
        "provider": llm.provider,
        # Single "primary" routed provider so frontends can echo it back on the
        # next turn's assistant history item as `provider`, giving the
        # orchestrator a deterministic stickiness signal (see RCA: silent
        # swiggy_food fallback). Falls back to the first routed provider in
        # alphabetical order when multiple providers ran (rare: compare flows).
        "routed_provider": (next(iter(sorted(routed))) if routed else None),
        "intent_routing": orchestration,
        "selected_tool_providers": sorted(routed),
        "food_routing_plan": plan,
        "tool_execution": {
            "requested": bool(execute_tools),
            "routed_providers": (
                domain_execution.get("routed_providers", [])
                if isinstance(domain_execution, dict)
                else (
                    tool_execution.get("routed_providers", [])
                    if isinstance(tool_execution, dict)
                    else []
                )
            ),
            "ok": (
                domain_execution.get("ok")
                if isinstance(domain_execution, dict)
                else (tool_execution.get("ok") if isinstance(tool_execution, dict) else False)
            ),
            "error": (
                domain_execution.get("error")
                if isinstance(domain_execution, dict)
                else (tool_execution.get("error") if isinstance(tool_execution, dict) else None)
            ),
            "error_code": (
                domain_execution.get("error_code")
                if isinstance(domain_execution, dict)
                else None
            ),
        },
        "calendar_execution": calendar_execution,
        "maps_execution": maps_execution,
        "observable_trace": build_tool_execution_trace(
            plan=plan if isinstance(plan, dict) else {},
            selected_providers=routed,
            tool_execution=(
                tool_execution
                if execute_tools and task_type not in {"reminder_calendar", "maps_query"}
                else None
            ),
            intent_routing=orchestration,
            calendar_execution=calendar_execution if execute_tools else None,
        ),
        "zomato_summary": zomato_summary,
        "provider_summaries": provider_summaries,
        "attachments": attachments,
        # Frontends should treat this as opaque and echo it back on the next
        # turn's request. Keys are the placeholder names the planner emits
        # (__CART_ID__, __RESTAURANT_ID__, __ORDER_ID__, ...); values are the
        # IDs the executor learned from MCP responses.
        "session_state": next_session_state,
        # Multi-restaurant memory — see ``run_agent_turn`` docstring. Echo
        # back as-is on the next turn's request.
        "session_restaurants": next_session_restaurants,
    }
    trace(
        "response_builder.in",
        task_type=task_type,
        attachment_count=len(attachments),
        raw_tool_results_summary={
            p: {
                "ok": (r or {}).get("ok"),
                "steps": len((r or {}).get("steps") or []),
                "skipped_tools": ((r or {}).get("diagnostics") or {}).get("skipped_tools"),
            }
            for p, r in (raw_tool_results or {}).items()
        },
    )
    if task_type == "normal_query":
        assistant_message = general_chat_responder.reply_general_chat(
            user_message=user_message,
            llm=llm,
            conversation_history=conversation_history,
        )
    elif task_type == "reminder_calendar":
        assistant_message = reply_reminder_calendar(
            user_message=user_message,
            execution=calendar_execution,
            execute_tools=execute_tools,
        )
    elif task_type == "maps_query":
        assistant_message = reply_maps_query(
            user_message=user_message,
            execution=maps_execution,
            execute_tools=execute_tools,
        )
    else:
        assistant_message = build_assistant_message_with_llm(
            user_message=user_message,
            plan=plan,
            tool_results=raw_tool_results,
            provider=llm.provider,
            api_key=llm.api_key,
            model=llm.model,
            base_url=llm.base_url,
            conversation_history=conversation_history,
            attachments=attachments,
        )
    trace("response_builder.out", assistant_message=assistant_message)
    out["assistant_message"] = assistant_message
    trace(
        "pipeline.out",
        routed_provider=out.get("routed_provider"),
        attachment_count=len(out.get("attachments") or []),
        tool_execution_ok=(out.get("tool_execution") or {}).get("ok"),
        tool_execution_error=(out.get("tool_execution") or {}).get("error"),
    )
    return out
