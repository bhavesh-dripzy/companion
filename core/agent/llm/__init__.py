"""LLM roles (Sarvam). Each agent's prompt + function live in its own module:

  router.py            — route()             : pick the domain
  grocery_brain.py     — grocery_brain()     : decide actions + ack (grocery, 1 call/turn)
  grocery_response.py  — grocery_response()  : narrate the real outcomes (grocery)
  news_brain.py        — news_brain()        : news query + mode (list/explain) + ack
  news_response.py     — news_response()     : explain the news, grounded in descriptions
  flights_brain.py     — flights_brain()     : flight params + ack/ask (flights, 1 call/turn)
  hotels_brain.py      — hotels_brain()      : hotel params + ack/ask (hotels, 1 call/turn)
  roles.py             — resolve(), narrate() : legacy, used by the tap path + food domain

Language only — never call MCP.
"""

from core.agent.llm.flights_brain import flights_brain
from core.agent.llm.grocery_brain import grocery_brain
from core.agent.llm.grocery_response import grocery_response
from core.agent.llm.hotels_brain import hotels_brain
from core.agent.llm.news_brain import news_brain
from core.agent.llm.news_response import news_response
from core.agent.llm.roles import narrate, resolve
from core.agent.llm.router import route

__all__ = [
    "flights_brain",
    "grocery_brain",
    "grocery_response",
    "hotels_brain",
    "news_brain",
    "news_response",
    "narrate",
    "resolve",
    "route",
]
