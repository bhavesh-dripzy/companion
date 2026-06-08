"""Swiggy restaurant-card builders (clean port of the legacy collectors).

Ported from `agents.pipeline._build_swiggy_restaurant_card_entry` /
`_collect_swiggy_restaurant_attachments`, adapted to operate on `ToolResult`s.
Emits the same `restaurant_cards` shape as Zomato (so the frontend renders both
interchangeably) but reads Swiggy's `data.restaurants[]` envelope and stamps
`provider: "swiggy"` for branding. Validated against a real captured fixture.
"""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult

_RESTAURANT_CARD_TOOLS = {"search_restaurants"}
_MAX_RESTAURANT_CARDS = 8
_IMG_PREFIX = "https://media-assets.swiggy.com/swiggy/image/upload/"


def _card_entry(resto: Any) -> dict[str, Any] | None:
    """Normalize one Swiggy `data.restaurants[]` row (id + name required)."""
    if not isinstance(resto, dict):
        return None
    rid_raw = resto.get("id") or resto.get("restaurant_id") or resto.get("res_id")
    name = resto.get("name") or resto.get("restaurant_name")
    if rid_raw is None or not isinstance(name, str) or not name.strip():
        return None
    rid = str(rid_raw).strip()
    if not rid:
        return None
    out: dict[str, Any] = {"res_id": rid, "name": name.strip()}
    rating = resto.get("avgRating") or resto.get("rating")
    if isinstance(rating, int | float) and rating > 0:
        out["rating"] = round(float(rating), 1)
    votes = resto.get("totalRatings") or resto.get("votes")
    if isinstance(votes, str) and votes.strip():
        out["votes"] = votes.strip()
    elif isinstance(votes, int | float) and votes > 0:
        out["votes"] = int(votes)
    eta = (
        resto.get("deliveryTimeRange")
        or resto.get("eta")
        or (
            f"{resto.get('deliveryTimeMinutes')} MINS"
            if isinstance(resto.get("deliveryTimeMinutes"), int | float)
            else None
        )
    )
    if isinstance(eta, str) and eta.strip():
        out["eta"] = eta.strip()
    dist = resto.get("distanceKm") or resto.get("distance_km") or resto.get("distance")
    if isinstance(dist, int | float) and dist >= 0:
        out["distance_km"] = round(float(dist), 2)
    image = resto.get("imageUrl") or resto.get("res_image") or resto.get("image_url")
    if isinstance(image, str) and image.strip():
        url = image.strip()
        out["res_image"] = url if url.startswith(("http://", "https://")) else f"{_IMG_PREFIX}{url}"
    offer = resto.get("offer") or resto.get("res_offer")
    if isinstance(offer, str) and offer.strip():
        out["res_offer"] = offer.strip()
    avail = resto.get("availabilityStatus") or resto.get("serviceability_status")
    if isinstance(avail, str) and avail.strip():
        out["serviceability_status"] = avail.strip()
    cuisines = resto.get("cuisines")
    if isinstance(cuisines, list):
        clean = [c.strip() for c in cuisines if isinstance(c, str) and c.strip()]
        if clean:
            out["cuisines"] = clean[:4]
    return out


def restaurant_entries(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Deduped, capped restaurant entries from one `search_restaurants` envelope."""
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    restos = data.get("restaurants") if isinstance(data.get("restaurants"), list) else []
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for resto in restos:
        if len(cards) >= _MAX_RESTAURANT_CARDS:
            break
        entry = _card_entry(resto)
        if entry is None or entry["res_id"] in seen:
            continue
        seen.add(entry["res_id"])
        cards.append(entry)
    return cards


def build_restaurant_cards(results: list[ToolResult]) -> list[Attachment]:
    out: list[Attachment] = []
    for tr in results:
        if tr.tool not in _RESTAURANT_CARD_TOOLS or not tr.ok:
            continue
        envelope = tr.data if isinstance(tr.data, dict) else {}
        if envelope.get("is_error"):
            continue
        cards = restaurant_entries(envelope)
        if not cards:
            continue
        payload: dict[str, Any] = {
            "kind": "restaurant_cards",
            "source": f"swiggy_food.{tr.tool}",
            "provider": "swiggy",
            "restaurants": cards,
        }
        keyword = (tr.arguments or {}).get("query") or (tr.arguments or {}).get("keyword")
        if isinstance(keyword, str) and keyword.strip():
            payload["keyword"] = keyword.strip()
        out.append(Attachment(**payload))
    return out
