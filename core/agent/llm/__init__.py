"""LLM roles (Sarvam): Router, Resolver, Narrator. Language only — never call MCP."""

from core.agent.llm.roles import narrate, resolve, route

__all__ = ["narrate", "resolve", "route"]
