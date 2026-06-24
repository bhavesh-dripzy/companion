"""The single registration point. Adding a provider = one line here (ADR-style).

Domain ↔ Provider is many-to-many (a provider can serve several domains; a domain has several
providers — grocery → Instamart + Zepto). The thin Router only ever needs the domain list.
"""

from __future__ import annotations

from core.agent.providers.base import ProviderAdapter
from core.agent.providers.instamart import InstamartProvider
from core.agent.providers.zepto import ZeptoProvider


class Registry:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderAdapter] = {}
        self._by_domain: dict[str, list[str]] = {}

    def register(self, adapter: ProviderAdapter) -> None:
        self._providers[adapter.id] = adapter
        for d in adapter.domains:
            self._by_domain.setdefault(d, []).append(adapter.id)

    def provider(self, provider_id: str) -> ProviderAdapter | None:
        return self._providers.get(provider_id)

    def providers_for(self, domain: str) -> list[ProviderAdapter]:
        return [self._providers[p] for p in self._by_domain.get(domain, [])]

    def domains(self) -> list[str]:
        return sorted(self._by_domain)


def build_registry() -> Registry:
    r = Registry()
    r.register(InstamartProvider())  # ← add new providers here, one line each
    r.register(ZeptoProvider())
    return r


REGISTRY = build_registry()
