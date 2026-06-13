"""Generate the planner prompt's tool list from `Tool`s.

Replaces the hand-written markdown bullet lists and `_system_prompt_for_tool_provider`
(ADR-0002). A provider ships a behavioural preamble with a `{{TOOLS}}` slot; this
renders the authoritative tool list (names, arg hints, confirmation language) into it.
"""

from __future__ import annotations

from core.contracts.tool import ConfirmPolicy, Tool

_TOOLS_SLOT = "{{TOOLS}}"

_CONFIRM_NOTE = {
    ConfirmPolicy.SINGLE: " [needs explicit user confirmation]",
    ConfirmPolicy.TWO_STEP: " [REAL ACTION — mandatory two-step user confirmation]",
}

# Shared location-continuity rule. Auto-attached to any planner whose preamble
# opts into geo by documenting `requested_location_text`, so every
# location-aware provider inherits the same carry-forward behaviour instead of
# re-deriving it (prod chats 5c22086f / 613cca04: "movies in delhi" then
# "showtimes near me" lost the Delhi context and failed with "I don't know your
# location"). Non-geo planners (kite, health) never reference the constraint, so
# they don't get this noise.
_LOCATION_CONTINUITY_MARKER = "requested_location_text"
_LOCATION_CONTINUITY = (
    "\nLOCATION CONTINUITY (carry the named place forward):\n"
    '- "near me", "nearby", "around me", "here", "close by", "in my area" are NOT '
    "places — NEVER copy these phrases into constraints.requested_location_text "
    "(they geocode to nothing).\n"
    "- When THIS message uses one of those phrases (or names no place at all), set "
    "constraints.requested_location_text to the most recent ACTUAL locality the "
    "user NAMED earlier in this conversation. Example: an earlier "
    '"movies in Delhi" then "showtimes near me" → requested_location_text="Delhi" '
    '(NOT "near me").\n'
    "- Only leave requested_location_text null when the user has NEVER named a real "
    "place anywhere in the conversation — then ambient GPS is the genuine fallback.\n"
    "- A real place named in THIS message always wins over an earlier one.\n"
)


def _arg_hint(tool: Tool) -> str:
    """Compact argument hint from the tool's JSON-Schema properties."""
    props = tool.params.get("properties") if isinstance(tool.params, dict) else None
    if not isinstance(props, dict) or not props:
        return ""
    required = set(tool.params.get("required") or [])
    names = [f"{name}*" if name in required else name for name in props]
    return f" (args: {', '.join(names)})"


def render_tools_block(tools: list[Tool]) -> str:
    """Render the `- tool_name: description (args: ...)` list shown to the planner."""
    lines: list[str] = []
    for tool in sorted(tools, key=lambda t: t.name):
        desc = f": {tool.description}" if tool.description else ""
        note = _CONFIRM_NOTE.get(tool.confirm, "")
        lines.append(f"- {tool.name}{desc}{_arg_hint(tool)}{note}")
    return "\n".join(lines)


def build_planner_prompt(preamble: str, tools: list[Tool]) -> str:
    """Fill the preamble's `{{TOOLS}}` slot (or append a TOOLS section).

    Location-aware planners (those documenting `requested_location_text`) inherit
    the shared location-continuity rule automatically.
    """
    if _LOCATION_CONTINUITY_MARKER in preamble and "LOCATION CONTINUITY" not in preamble:
        preamble = f"{preamble.rstrip()}\n{_LOCATION_CONTINUITY}"
    block = render_tools_block(tools)
    if _TOOLS_SLOT in preamble:
        return preamble.replace(_TOOLS_SLOT, block)
    return f"{preamble.rstrip()}\n\n" "TOOLS (use ONLY these exact tool names):\n" f"{block}\n"
