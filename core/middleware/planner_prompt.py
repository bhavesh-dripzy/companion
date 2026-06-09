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
    """Fill the preamble's `{{TOOLS}}` slot (or append a TOOLS section)."""
    block = render_tools_block(tools)
    if _TOOLS_SLOT in preamble:
        return preamble.replace(_TOOLS_SLOT, block)
    return f"{preamble.rstrip()}\n\n" "TOOLS (use ONLY these exact tool names):\n" f"{block}\n"
