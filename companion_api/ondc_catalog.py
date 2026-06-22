"""Parse an ONDC on_search catalog into a normalized provider/item shape.

Tolerant of both retail versions: v1.2.x nests providers under
``catalog["bpp/providers"]`` while 2.x uses ``catalog["providers"]``; item price
and identifiers live in the same places. Returns just what the BAP needs to
render results and build a ``select`` (bpp routing + provider + items).
"""

from __future__ import annotations

from typing import Any


def _providers(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    return catalog.get("providers") or catalog.get("bpp/providers") or []


def parse_catalog(on_search_body: dict[str, Any]) -> dict[str, Any]:
    """Normalize an on_search body to {bpp_id, bpp_uri, transaction_id, providers[]}."""
    context = on_search_body.get("context") or {}
    catalog = (on_search_body.get("message") or {}).get("catalog") or {}

    providers: list[dict[str, Any]] = []
    for provider in _providers(catalog):
        items = []
        for item in provider.get("items") or []:
            price = item.get("price") or {}
            items.append(
                {
                    "id": item.get("id"),
                    "name": (item.get("descriptor") or {}).get("name"),
                    "price": price.get("value"),
                    "currency": price.get("currency", "INR"),
                    "location_id": item.get("location_id"),
                    "fulfillment_id": item.get("fulfillment_id"),
                }
            )
        providers.append(
            {
                "id": provider.get("id"),
                "name": (provider.get("descriptor") or {}).get("name"),
                "items": items,
            }
        )

    return {
        "bpp_id": context.get("bpp_id"),
        "bpp_uri": context.get("bpp_uri"),
        "transaction_id": context.get("transaction_id"),
        "providers": providers,
    }
