"""Response-builder agent.

Takes the **raw** tool-execution output (MCP responses) and asks the LLM to write
the user-facing reply. There is intentionally no domain-specific extraction
here — the LLM reads the raw structured tool output directly so this works for
food, groceries, calendar/reminder lookups, medical queries, or any future
provider without code changes.

Only two helpers remain:
- ``saved_address_label_from_mcp_item`` — small utility used by other modules
  (``companion_api/main.py``, swiggy/zomato tests) to format saved-address rows.
- ``wrap_tool_result`` (alias: ``build_zomato_summary`` for back-compat) —
  thin ``{ok, error, raw, diagnostics}`` wrapper around a raw MCP result so the
  API response envelope still surfaces success/failure cleanly without doing
  any lossy extraction.
"""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from app.services.debug_trace import snip, trace


def saved_address_label_from_mcp_item(item: dict[str, Any]) -> str:
    """
    Human-facing label for one saved-address row from MCP (Zomato, Swiggy, etc.).

    Swiggy Food ``get_addresses`` uses camelCase: ``addressLine``, ``addressTag``,
    ``addressCategory``, ``id`` — not ``location_name`` / ``address_line``.
    """
    if not isinstance(item, dict):
        return ""
    row_id = str(item.get("address_id") or item.get("addressId") or item.get("id") or "").strip()

    def ok(text: str) -> bool:
        t = (text or "").strip()
        return bool(t) and t != row_id

    tag = str(item.get("addressTag") or item.get("address_tag") or "").strip()
    category = str(item.get("addressCategory") or item.get("address_category") or "").strip()
    line = str(
        item.get("addressLine")
        or item.get("address_line")
        or item.get("formatted_address")
        or item.get("formattedAddress")
        or item.get("location_name")
        or item.get("name")
        or item.get("label")
        or ""
    ).strip()

    if tag and line:
        return f"{tag} — {line}" if ok(line) else tag
    if line and ok(line):
        return line
    if tag:
        return tag
    if category and line:
        return f"{category}: {line}"
    if category:
        return category
    for key in ("location_name", "name", "label"):
        v = str(item.get(key) or "").strip()
        if ok(v):
            return v
    return ""


def wrap_tool_result(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Thin pass-through wrapper around a raw MCP/tool result.

    Surfaces ``ok``, ``error``, and ``diagnostics`` at the top level so the
    response envelope and downstream consumers don't have to dig, while
    preserving the full raw payload under ``raw_result`` for the response LLM
    to read directly.
    """
    if not isinstance(raw, dict):
        return {
            "ok": False,
            "error": "tool_not_executed",
            "raw_result": None,
            "diagnostics": {},
        }
    return {
        "ok": bool(raw.get("ok")),
        "error": None if raw.get("ok") else str(raw.get("error_detail") or "unknown_error"),
        "raw_result": raw,
        "diagnostics": raw.get("diagnostics") if isinstance(raw.get("diagnostics"), dict) else {},
    }


def build_zomato_summary(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Backwards-compatible alias for ``wrap_tool_result``.

    Existing callers (pipeline, food_agent view, API response envelope) still
    import this name; the implementation no longer performs any extraction.
    """
    return wrap_tool_result(raw)


def _format_conversation_history(
    conversation_history: list[dict[str, str]] | None,
) -> str | None:
    """Render the last few turns of conversation as a compact transcript."""
    if not conversation_history:
        return None
    recent = [
        item
        for item in conversation_history[-8:]
        if isinstance(item, dict)
        and item.get("role") in {"user", "assistant"}
        and item.get("content")
    ]
    if not recent:
        return None
    return "\n".join(
        f"{item.get('role')}: {str(item.get('content') or '')[:700]}"
        for item in recent
    )


def _any_tool_succeeded(tool_results: dict[str, Any] | None) -> bool:
    if not isinstance(tool_results, dict):
        return False
    for value in tool_results.values():
        if isinstance(value, dict) and value.get("ok"):
            return True
    return False


def _fallback_assistant_message(
    *,
    tool_results: dict[str, Any] | None,
) -> str:
    """Domain-neutral safety-net reply used only when the response LLM call fails.

    Real phrasing lives in the LLM prompt; this is intentionally minimal.
    """
    if not _any_tool_succeeded(tool_results):
        return (
            "I tried to take care of that for you, but couldn't finish it this time. "
            "Want me to try again, or would you like to refine the request?"
        )
    return (
        "I've got something back for you on that. "
        "Should I show more details, refine the search, or move ahead?"
    )


def _parse_response_llm_output(raw: str) -> tuple[str, list[str] | None]:
    """Split the response LLM's JSON output into ``(text, featured_variant_ids)``.

    Contract is ``{"text": "...", "featured_variant_ids": ["v_…", ...]}``.

    Tolerance:
      * If the model wraps the JSON in a markdown fence, strip it.
      * If the model returns plain text (no JSON), treat the whole thing as
        ``text`` and return ``None`` for the featured list so the pipeline
        knows not to apply LLM-driven filtering (falls back to showing all
        cards rather than hiding them all).
      * If JSON is present but ``featured_variant_ids`` is missing or not a
        list, treat as empty list (== explicit "no cards").
      * Always coerce featured_variant_ids to a list[str] and drop blanks
        — that's what the pipeline filter expects.
    """
    if not isinstance(raw, str) or not raw.strip():
        return "", None
    s = raw.strip()
    # Strip ```json … ``` / ``` … ``` fences if the LLM hedged.
    if s.startswith("```"):
        # Drop leading fence (and optional language tag), then trailing fence.
        first_newline = s.find("\n")
        if first_newline != -1:
            s = s[first_newline + 1 :]
        if s.endswith("```"):
            s = s[: -3]
        s = s.strip()
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return raw.strip(), None
    if not isinstance(obj, dict):
        return raw.strip(), None
    text = obj.get("text")
    if not isinstance(text, str):
        return raw.strip(), None
    feat_raw = obj.get("featured_variant_ids")
    feat: list[str] = []
    if isinstance(feat_raw, list):
        for v in feat_raw:
            if isinstance(v, str) and v.strip():
                feat.append(v.strip())
    return text.strip(), feat


def _openai_compatible_chat(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    base_url: str | None = None,
) -> str:
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    completion = client.chat.completions.create(
        model=model,
        temperature=0.4,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return (completion.choices[0].message.content or "").strip()


_SYSTEM_PROMPT = (
    "You are the user's personal AI voice assistant. Your job is to take action on "
    "their behalf — across any domain they ask about (ordering food or groceries, "
    "scheduling, reminders, general questions, medical or health queries, lookups, "
    "planning, research, and more). One or more of your connected tools has just "
    "run for the user, and the structured tool output is given to you as JSON. "
    "Write the spoken reply.\n"
    "\n"
    "## Output format (STRICT — read first)\n"
    "Return **exactly one JSON object**, no markdown fences, no preamble, no "
    "trailing commentary. The schema is:\n"
    "```\n"
    "{\n"
    "  \"text\": \"<the spoken reply for the user>\",\n"
    "  \"featured_variant_ids\": [\"v_111\", \"v_222\", ...]\n"
    "}\n"
    "```\n"
    "- `text` — the natural-language reply (Hinglish/English to match the user). All "
    "  styling guidance below applies to this field.\n"
    "- `featured_variant_ids` — the variant_ids you want the UI to surface as visual "
    "  cards under your bubble. The user only sees a card for an item if its "
    "  variant_id is in this list. Rules:\n"
    "  • If the tool output contains a `menu_cards` attachment (visible in "
    "    `ui_attachments[].items_preview`, each row has its `variant_id`), pick only "
    "    the variant_ids of the items you actually mention or highlight in `text`. "
    "    Do NOT echo every available variant_id — the point is to show the user "
    "    only what your reply is about (e.g. user asked for veg → list only the veg "
    "    variant_ids you mentioned).\n"
    "  • If you discuss items generically without naming any specific ones, or no "
    "    menu_cards attachment is present, return `[]`.\n"
    "  • Only emit variant_ids that appear verbatim in `ui_attachments[].items_preview`. "
    "    Never invent ids.\n"
    "  • For non-menu replies (checkout confirmation, address nudge, error message, "
    "    general chat), return `[]`.\n"
    "\n"
    "How to write `text`:\n"
    "- Speak in first person, as the assistant who just did the work for them. "
    "Phrases in the spirit of \"Here's what I found for you on <provider>\", "
    "\"I checked <provider> and got these\", or \"Maine yeh dhundha aapke liye\" "
    "feel right. Make it sound like an assistant doing the work, not a search engine.\n"
    "- Use the conversation history (when provided) to stay coherent with what the "
    "user was already asking. Don't repeat yourself, don't re-ask things they "
    "already answered.\n"
    "- Match the user's language and register. If they wrote in Hinglish, reply in "
    "Hinglish. If they wrote in English, reply in English. Same for any other "
    "language. Keep it warm, natural, and short — this will be read out loud.\n"
    "- Treat the tool JSON as ground truth. Read it carefully — it can be deeply "
    "nested. Pull out whatever is genuinely useful to the user (names, prices, "
    "ratings, ETAs, addresses, times, dosages, links, identifiers the user would "
    "recognise, etc.). Do not invent anything that isn't there. If a field is "
    "missing or empty, don't fill it in.\n"
    "- Never expose raw JSON, field names, internal IDs the user wouldn't recognise, "
    "tool/provider IDs, MCP terminology, error codes, stack traces, or anything "
    "that sounds technical.\n"
    "- When the tool returned useful results, surface the top 6-8 most relevant "
    "items in one short list. For each item, mention the single most useful "
    "attribute available — pick whatever genuinely helps the user decide. The "
    "executor remembers every item it showed you this session via "
    "`session_menu` (up to 15 items in an LRU), so the user can pick any "
    "of them by name on a follow-up turn — including items beyond the "
    "first batch you surfaced. Don't truncate to 5 unless the user "
    "explicitly asked for fewer; *and* don't pad to 8 when there are "
    "only 3 genuinely relevant items. "
    "**Exception — menu_cards attachment**: when the input contains "
    "`ui_attachments[]` entries with `kind: \"menu_cards\"`, a Zomato-style "
    "visual card row (image, price, rating, veg/non-veg dot) is rendered "
    "right under your bubble — but **only for the variant_ids you list in "
    "`featured_variant_ids`**. So your `text` and your card list must match: "
    "every card the user sees is an item you're talking about, every item "
    "you name should have a card. **Do not repeat the list in markdown.** "
    "Write 2–4 sentences of companion-style guidance: call out 1–2 "
    "standout picks by name (and put their variant_ids in "
    "`featured_variant_ids`), explain *why* in one line (the cheapest, "
    "the highest-rated, the best deal, the healthiest, the bestseller — "
    "whatever signal is genuinely in the data), surface one real "
    "trade-off if one exists, and end with an open question (*\"anything "
    "jump out?\"*, *\"want me to pull up the salads next?\"*). Tone: a "
    "friend recommending dinner, not a waiter reading a menu. If the "
    "user filtered ('only veg', 'under ₹250'), apply the filter on the "
    "items_preview rows yourself — only mention and feature the items "
    "that match the filter.\n"
    "- **Customization defaults (Zomato)**: After a successful `create_cart`, "
    "look at `cart.items[].add_ons[]` in the tool result — that's the set of "
    "required modifiers Zomato auto-picked (e.g. bread, prep, veg, sauce on a "
    "Subway sandwich). When the array is non-empty, list the picks in one "
    "short, plain-English sentence — *\"Added Paneer Tikka 15cm with Roast "
    "Garlic bread, Toasted with Cheese Slice, Lettuce, Honey Mustard. Want "
    "any of those swapped?\"* — using the `name` and `add_on_group_name` "
    "fields. Never expose `ctl_…`, `group_id`, or `mg_…` ids. Empty `add_ons` "
    "means the item had no required customizations; just confirm the item "
    "was added.\n"
    "- **Variant choice (Zomato)**: The planner picks the literal child "
    "`variant_id` (size/crust/base) from `entry.variants[]` directly — the "
    "executor does no matching. When the cart response confirms a specific "
    "variant (look at `cart.items[].item_name` and any size-style suffix "
    "the menu surfaced), echo it in natural language — *\"Added a medium "
    "pan margherita.\"* — so the user knows we caught the size right.\n"
    "- **Customization changes the user asks for**: If the user requested a "
    "specific bread / sauce / topping / protein etc. but the cart's "
    "auto-picked `add_ons[]` doesn't match, tell them honestly — *\"I added "
    "the sandwich with the default picks (Roast Garlic, Toasted with Cheese, "
    "Lettuce, Honey Mustard). Custom modifier picks are temporarily limited; "
    "you can edit them on Zomato's checkout screen.\"* Don't pretend the "
    "customisation went through.\n"
    "- **Menu truncation breadcrumb**: if a tool result contains a "
    "`_menu_truncation` field (e.g. `{\"item_mappings\": {\"shown\": 8, "
    "\"total\": 164}}`), the menu you can see is a top-N slice of a larger "
    "menu. Tell the user the truth — phrase it like *“these are the top 8 "
    "of 164 items — want me to show a specific category?”* — rather than "
    "implying it's the full list. **Never** invent or mention items beyond "
    "the visible slice; the next-turn planner cannot resolve their "
    "variant_id and create_cart will go to the wrong item.\n"
    "- When the tool failed or returned nothing useful, acknowledge it gracefully "
    "in plain language, and give the user a clear next step.\n"
    "- **Unresolved placeholders — read the diagnostics literally.** If the "
    "tool execution was incomplete the diagnostics contain a `skipped_tools` "
    "list, each item carrying `tool`, `reason`, and `placeholders`. The "
    "missing placeholders tell you *exactly* what's wrong — do not pattern-"
    "match on the tool name and guess. Specifically:\n"
    "  - `__ADDRESS_ID__` missing → ask the user to share their delivery "
    "location or pick a saved address.\n"
    "  - `__CART_ID__` missing on a `checkout_cart` step → the cart no "
    "longer exists or was never created. Tell the user the cart wasn't "
    "found and offer to re-add the items (don't say 'address chahiye'; the "
    "address is fine, the cart is the problem).\n"
    "  - `__RESTAURANT_ID__` missing → the previous restaurant search "
    "returned nothing relevant. Ask the user which place they meant or "
    "offer to search again.\n"
    "  - `__ORDER_ID__` missing on a tracking call → ask the user which "
    "order they mean, or offer to fetch order history.\n"
    "Never blame the address when the missing placeholder list does not "
    "include `__ADDRESS_ID__`.\n"
    "- **Cart rows dropped (Zomato)**: If the diagnostics contain "
    "`create_cart_dropped_rows` (list of `{variant_id, quantity, reason}`), "
    "the planner emitted a `create_cart` row with a `variant_id` the "
    "executor refused to send — usually a leftover `__MENU_ITEM_ID__` "
    "placeholder or a `ctl_…` item_id confused for a variant_id. The "
    "remaining rows (if any) still went through. Tell the user honestly "
    "that the specific item couldn't be added (use the dropped row's "
    "context — name from session_menu if you can match it back), and offer "
    "to retry by showing the menu again. Don't blame the address, the "
    "cart, or the network.\n"
    "- **Variant choice required (Swiggy)**: If the diagnostics contain "
    "`pending_variant_choice` (list of items like `[{item_name, groups: "
    "[{name, choices: [{id, name, price}]}]}]`), the cart add was "
    "intentionally deferred because the menu item has multiple in-stock "
    "options the user must pick (size 8\"/10\"/12\", crust hand-tossed vs "
    "thin, etc.). Do NOT pretend the item was added. Instead, ask the "
    "user which option they want, listing the choice names + prices in a "
    "short numbered list. Example: *\"Margherita Pizza comes in 3 sizes — "
    "8 inch ₹279, 10 inch ₹559, 12 inch ₹729. Which one?\"*. Mention "
    "every group separately if there are multiple (e.g. Size + Crust). "
    "Never expose the raw `id`/`groupId` fields — those are for the next "
    "tool plan, not for the user.\n"
    "- **Cart auto-pick (Swiggy)**: If diagnostics contain "
    "`auto_variants_injected`, the executor silently picked a default "
    "variant (because there was only one in-stock option per group). "
    "Treat it as a normal successful add — no need to call attention to "
    "the variant unless the user asks.\n"
    "- **UI attachments**: When the input contains a non-empty `ui_attachments` "
    "list, the chat UI is rendering those images/files *next to* your message — "
    "the user will see the actual image. So acknowledge it briefly ('Yeh raha "
    "QR — ₹185 scan karke pay kar do' / 'Here's the payment QR — scan it from "
    "any UPI app for ₹185') and mention the amount/payee if present. Do NOT "
    "describe pixel-level visual contents (\"a black-and-white square pattern\") "
    "and do NOT paste base64 or embed image markdown — that's the frontend's "
    "job. If a `has_upi_link` is true, mention that the UPI deep-link is also "
    "available as a one-tap fallback.\n"
    "- **Address nudge**: The input may include an `address_context` block "
    "with `{label, source, options_count}`. Read `source` to decide whether "
    "to mention the address at all:\n"
    "  - `source = \"auto\"` (executor auto-picked the first saved address "
    "because the user didn't choose one) → add a short, friendly one-liner "
    "at the very end of the reply, after the follow-up suggestions, "
    "telling the user which address you used and that they can switch. "
    "Use the `label` field. Example (English): *\"(Using your saved "
    "address — Home, MG Road. Want me to use a different one? Say 'change "
    "address'.)\"*. Example (Hinglish): *\"(Aapke saved address pe order "
    "kar raha hoon — Home, MG Road. Doosre address pe karwana ho toh "
    "bolo 'change address'.)\"*. Keep it parenthetical / italicised — "
    "it's a footnote, not the main message.\n"
    "  - `source = \"session_addresses\"` (user has been on this address "
    "for ≥ 1 prior turn) → do NOT mention the address. They already know.\n"
    "  - `source = \"request\"` (user just picked an address in the UI) → "
    "do NOT mention the address.\n"
    "  - `source = \"session_state\"` (carried via session_state from a "
    "prior turn) → do NOT mention the address.\n"
    "  - `source = \"switched\"` (user just asked to switch addresses on "
    "this turn) → do NOT add the nudge footer; the user already chose. "
    "Briefly confirming the switch in the main body is fine (e.g. "
    "*\"Aapke Sector 38 Gurgaon address pe ...\"*), but don't repeat "
    "with a parenthetical footer.\n"
    "  - `address_context` absent / null → do NOT mention the address.\n"
    "  Never read out the raw `address_id`. Only the `label`.\n"
    "- **Dine-out nudge (Zomato/Swiggy restaurant queries only)**: When a "
    "Zomato or Swiggy_food restaurant search ran successfully for an "
    "ambiguous query (e.g. \"find restaurants near me\", \"good Italian "
    "places\", \"biryani near Koramangala\") and the user did NOT "
    "explicitly say they wanted delivery (\"order\", \"deliver\", "
    "\"home delivery\", \"cart\"), include ONE short closing question "
    "offering the going-out path as an alternative. Examples: *\"Or were "
    "you thinking of going out / dining in? I can pull up table-booking "
    "options too.\"*, *\"Ya phir bahar khaane jaane ka plan hai? "
    "Table booking bhi karwa sakta hoon.\"*. Keep it to ONE sentence at "
    "the end. Do NOT add this nudge when the user clearly asked for "
    "delivery (e.g. \"order pizza\", \"deliver biryani\", \"send food to "
    "my home\"), when the query was already routed to District, or when "
    "it's a grocery query.\n"
    "- Always end with a short, interactive follow-up — 2 to 4 concrete spoken "

    "suggestions the user can reply with, chosen to fit the data and the user's "
    "likely intent. They should feel like things a helpful assistant would offer "
    "next, e.g. \"should I show you the full menu of <X>?\", \"want me to filter "
    "only veg?\", \"shall I add this to your cart?\", \"kuch aur dhundhun?\", "
    "\"aapka kuch khaane ka man hai?\", \"should I book this for you?\", etc. — "
    "always adapted to the actual domain and result.\n"
    "- Keep the whole reply short enough to feel comfortable as a voice response."
)


def _summarize_attachments_for_prompt(
    attachments: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Trim attachments to a description-only view (no base64) for the LLM.

    The response LLM only needs to know *that* an image is being shown and
    *what kind* — it must NOT receive the raw base64 (token cost + temptation
    to paste it back into the reply). The frontend renders the actual image
    from the un-trimmed ``attachments`` field on the agent-turn payload.
    """
    if not isinstance(attachments, list):
        return []
    summarized: list[dict[str, Any]] = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        kind = str(att.get("kind") or "")
        if kind == "menu_cards":
            # Card attachments don't carry base64; the LLM gets a thin per-item
            # summary so it can name standout picks without parroting the full
            # list. Crucially, we now ship ``variant_id`` per row so the LLM
            # can echo it back in ``featured_variant_ids`` — the pipeline uses
            # that list to filter which cards actually reach the UI. Without
            # variant_id, the LLM can't do the filtering contract. Everything
            # else (image, description) stays on the frontend side.
            items = att.get("items") or []
            preview: list[dict[str, Any]] = []
            if isinstance(items, list):
                for it in items[:25]:
                    if not isinstance(it, dict):
                        continue
                    vid = it.get("variant_id")
                    if not (isinstance(vid, str) and vid.strip()):
                        continue
                    row: dict[str, Any] = {
                        "variant_id": vid.strip(),
                        "name": it.get("name"),
                    }
                    for k in ("price", "discounted_price", "rating", "votes", "veg_kind", "category"):
                        if it.get(k) not in (None, "", 0):
                            row[k] = it[k]
                    preview.append(row)
            summarized.append(
                {
                    "kind": "menu_cards",
                    "source": att.get("source"),
                    "restaurant_name": att.get("restaurant_name"),
                    "categories": att.get("categories"),
                    "items_count": len(items) if isinstance(items, list) else 0,
                    "items_preview": preview,
                }
            )
            continue
        summarized.append(
            {
                "kind": att.get("kind"),
                "source": att.get("source"),
                "mime_type": att.get("mime_type"),
                "caption": att.get("caption"),
                "alt": att.get("alt"),
                "amount_inr": att.get("amount_inr"),
                "payee_name": att.get("payee_name"),
                "transaction_ref": att.get("transaction_ref"),
                "has_upi_link": bool(att.get("upi_link")),
                "has_inline_image": bool(att.get("data_b64")),
            }
        )
    return summarized


def build_assistant_message_with_llm(
    *,
    user_message: str,
    plan: dict[str, Any] | None,
    tool_results: dict[str, Any] | None,
    provider: str,
    api_key: str,
    model: str,
    base_url: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    address_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate the final assistant reply from raw tool output.

    Returns
    -------
    dict
        ``{"text": <spoken reply>, "featured_variant_ids": [<v_id>, ...] | None}``.
        ``featured_variant_ids`` is a (possibly empty) list when the LLM
        complied with the JSON contract, or ``None`` when it fell back to
        plain text (so the caller can decide whether to filter cards or
        leave the legacy "show everything" behaviour intact).

    Parameters
    ----------
    user_message:
        Current user turn (just this turn, untransformed).
    plan:
        Planner output (intent / summary / constraints) from the domain agent —
        gives the response LLM context on *why* the tool was called.
    tool_results:
        Mapping of provider id (e.g. ``"zomato"``, ``"swiggy_food"``) to the
        **raw** MCP/tool result. No extraction is done here — the LLM reads it.
    conversation_history:
        Recent ``user`` / ``assistant`` turns so the reply stays coherent with
        the ongoing conversation.
    attachments:
        Optional list of image/file attachments the chat UI will render next
        to this reply (e.g. a Zomato payment QR). The LLM is told they will
        appear so the spoken reply can reference them ("here's the QR…")
        without describing visual content in detail.
    """
    history_transcript = _format_conversation_history(conversation_history)

    plan_context: dict[str, Any] | None = None
    if isinstance(plan, dict):
        plan_context = {
            "intent": plan.get("intent"),
            "summary": plan.get("summary"),
            "constraints": plan.get("constraints"),
        }

    attachment_descriptors = _summarize_attachments_for_prompt(attachments)

    # Sanitize address_context so we only ship the fields the LLM is
    # allowed to surface — never the raw address_id.
    safe_address_context: dict[str, Any] | None = None
    if isinstance(address_context, dict):
        safe_address_context = {
            "label": address_context.get("label"),
            "source": address_context.get("source"),
            "options_count": address_context.get("options_count"),
        }

    payload: dict[str, Any] = {
        "current_user_message": user_message,
        "conversation_history": history_transcript,
        "planner_context": plan_context,
        "tool_results": tool_results or {},
        "ui_attachments": attachment_descriptors,
        "address_context": safe_address_context,
    }

    user_prompt = (
        "Here is the structured tool output along with conversation context. "
        "Reply with the JSON object described in the system prompt — "
        "`{\"text\": \"...\", \"featured_variant_ids\": [...]}` — and nothing else:\n"
        + json.dumps(payload, ensure_ascii=True, default=str)
    )
    trace(
        "response_llm.in",
        provider=provider,
        model=model,
        ui_attachments=attachment_descriptors,
        address_context=safe_address_context,
        tool_results=snip(payload.get("tool_results")),
        planner_context=payload.get("planner_context"),
        user_message=user_message,
    )

    def _fallback() -> dict[str, Any]:
        return {
            "text": _fallback_assistant_message(tool_results=tool_results),
            "featured_variant_ids": None,
        }

    try:
        if provider in ("openai", "deepseek"):
            raw = _openai_compatible_chat(
                api_key=api_key,
                model=model,
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                base_url=base_url if provider == "deepseek" else None,
            )
        else:
            from google import genai as google_genai

            client = google_genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=model,
                contents=f"SYSTEM:\n{_SYSTEM_PROMPT}\n\nUSER:\n{user_prompt}",
            )
            raw = (response.text or "").strip()

        if not raw:
            trace("response_llm.fallback", reason="empty_text", provider=provider)
            return _fallback()

        text, featured = _parse_response_llm_output(raw)
        if not text:
            trace("response_llm.fallback", reason="empty_text_after_parse", provider=provider)
            return _fallback()
        trace(
            "response_llm.out",
            provider=provider,
            text=text,
            featured_variant_ids=featured,
            parsed=featured is not None,
        )
        return {"text": text, "featured_variant_ids": featured}
    except Exception as e:
        trace("response_llm.error", provider=provider, error=f"{type(e).__name__}: {e}")
        return _fallback()
