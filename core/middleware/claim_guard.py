"""Deterministic backstop: the narrator may not claim a mutation the tools didn't apply.

S1's MUTATION_APPLIED prompt rule is advisory, and the production narrator LLM
ignores it - confabulating "I've added 2x milk, Rs154" on a turn where no mutating
tool ran (Instamart prod audit, 2026-06). This is the structural guard: if the
reply asserts a state change (added / removed / ordered / switched / ...) but NO
tool result this turn is tagged APPLIED, the claim is unsupported and we replace it
with an honest, situation-aware message. Generic across providers - keyed on
ToolResult.effect (set from Tool.mutating), so every mutating surface inherits it.
"""

from __future__ import annotations

import re

from core.contracts.tool_result import Effect, ProviderResult

# First-person past-tense completion claims that assert a state change took effect.
_CLAIM_RE = re.compile(
    r"\b(i'?ve|i have|i)\s+(just\s+|now\s+|already\s+)?"
    r"(added|removed|deleted|cleared|emptied|updated|changed|switched|moved|"
    r"placed|ordered|booked|reserved|created|scheduled|sent|cancelled|canceled)\b",
    re.IGNORECASE,
)


def _applied(results: list[ProviderResult]) -> bool:
    return any(tr.effect is Effect.APPLIED for pr in results for tr in pr.results)


def _ran_and_failed(results: list[ProviderResult]) -> bool:
    return any(not tr.ok for pr in results for tr in pr.results)


def unsupported_mutation_claim(message: str, results: list[ProviderResult]) -> bool:
    """True if the reply asserts a state change that no tool actually applied this turn."""
    if not message or _applied(results):
        return False
    return bool(_CLAIM_RE.search(message))


def honest_message(results: list[ProviderResult], has_cards: bool) -> str:
    """A truthful reply for a turn that claimed a change nothing actually made."""
    if has_cards:
        return (
            "I couldn't make that change just now — but here are the options above. "
            "Tell me exactly which one (and the platform) and I'll do it."
        )
    if _ran_and_failed(results):
        return "That didn't go through — the request hit an error. Want me to try again?"
    return (
        "I wasn't able to do that just now. Could you tell me the specific item and "
        "platform, and I'll try again?"
    )
