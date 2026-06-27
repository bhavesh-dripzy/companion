"""LLM roles (Sarvam). Each agent's prompt + function live in its own module:

  router.py            — route()             : pick the domain
  grocery_brain.py     — grocery_brain()     : decide actions + ack (grocery, 1 call/turn)
  grocery_response.py  — grocery_response()  : narrate the real outcomes (grocery)
  roles.py             — resolve(), narrate() : legacy, used by the tap path + food domain

Language only — never call MCP.
"""

from core.agent.llm.grocery_brain import grocery_brain
from core.agent.llm.grocery_response import grocery_response
from core.agent.llm.roles import narrate, resolve
from core.agent.llm.router import route

__all__ = ["grocery_brain", "grocery_response", "narrate", "resolve", "route"]
