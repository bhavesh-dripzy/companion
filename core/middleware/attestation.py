"""Write-effect attestation: tag every mutating tool result as applied or not.

The narrator may only assert a state change (cart updated, order placed, address
switched, call made) that a tool result proves. Reads carry no claim (effect stays
None); a mutating call carries APPLIED when it returned a non-error envelope and
NOT_APPLIED when it failed, timed out, or was otherwise unsuccessful. Keyed on the
long-standing `Tool.mutating` flag, so every provider's writes inherit the gate —
no per-tool prompt rule. A turn that ran no mutating tool simply has no APPLIED
result, which the narrator reads as "nothing was changed."
"""

from __future__ import annotations

from core.contracts.tool_result import Effect, ProviderResult
from core.registry.tool_registry import ToolRegistry


def attest(results: list[ProviderResult], tools: ToolRegistry) -> None:
    """Set `effect` in place on every mutating tool result across all providers."""
    for provider_result in results:
        for tr in provider_result.results:
            tool = tools.get(tr.tool, provider=tr.provider) or tools.get(tr.tool)
            if tool is None or not tool.mutating:
                continue
            tr.effect = Effect.APPLIED if tr.ok else Effect.NOT_APPLIED
