"""Render `Tool`s into each LLM vendor's native function-calling schema.

Future-proofs a move from prompt-listed tools to native function calling, and is
the single place the voice `companion_execute` / Realtime / Gemini-Live tool
definitions are generated from (ADR-0002).
"""

from __future__ import annotations

from typing import Any, Literal

from core.contracts.tool import Tool

Format = Literal["openai", "gemini", "anthropic"]

_EMPTY_OBJECT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


def _params(tool: Tool) -> dict[str, Any]:
    return tool.params or dict(_EMPTY_OBJECT_SCHEMA)


def to_openai_tool(tool: Tool) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": _params(tool),
        },
    }


def to_gemini_declaration(tool: Tool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": _params(tool),
    }


def to_anthropic_tool(tool: Tool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": _params(tool),
    }


_RENDERERS = {
    "openai": to_openai_tool,
    "gemini": to_gemini_declaration,
    "anthropic": to_anthropic_tool,
}


def tools_for_llm(tools: list[Tool], fmt: Format) -> list[dict[str, Any]]:
    """Render a list of tools into the given vendor's function-calling format."""
    render = _RENDERERS[fmt]
    return [render(tool) for tool in tools]
