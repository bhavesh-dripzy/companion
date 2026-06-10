"""Amazon Product API (engine=amazon_product) — params + lean extraction.

Validated against a live amazon.in payload: price/discount/stock/delivery,
full-res image set, about_item bullets, spec table, and reviews_information
(an aspect-level sentiment summary + author reviews) — everything the
narrator needs to genuinely discuss one product."""

from __future__ import annotations

from typing import Any

_MAX_IMAGES = 5
_MAX_BULLETS = 6
_MAX_SPECS = 8
_MAX_INSIGHTS = 6
_MAX_AUTHOR_REVIEWS = 3


def product_details_params(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "engine": "amazon_product",
        "asin": str(args.get("asin") or "").strip(),
        "amazon_domain": "amazon.in",
    }


def _clean(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if v not in (None, "", [], {})}


def _trim(text: Any, cap: int) -> str:
    value = str(text or "").strip()
    return value if len(value) <= cap else value[: cap - 1] + "…"


def _review_block(payload: dict[str, Any]) -> dict[str, Any]:
    info = payload.get("reviews_information")
    if not isinstance(info, dict):
        return {}
    out: dict[str, Any] = {}
    summary = info.get("summary") if isinstance(info.get("summary"), dict) else {}
    if summary.get("text"):
        out["review_summary"] = _trim(summary["text"], 600)
    insights = []
    for row in (summary.get("insights") or [])[:_MAX_INSIGHTS]:
        if not isinstance(row, dict):
            continue
        mentions = row.get("mentions") if isinstance(row.get("mentions"), dict) else {}
        insights.append(
            _clean(
                {
                    "aspect": row.get("title"),
                    "sentiment": row.get("sentiment"),
                    "positive": mentions.get("positive"),
                    "negative": mentions.get("negative"),
                }
            )
        )
    if insights:
        out["review_insights"] = insights
    reviews = []
    for row in (info.get("authors_reviews") or [])[:_MAX_AUTHOR_REVIEWS]:
        if not isinstance(row, dict):
            continue
        reviews.append(
            _clean(
                {
                    "rating": row.get("rating"),
                    "title": _trim(row.get("title"), 80),
                    "text": _trim(row.get("body") or row.get("text") or row.get("review"), 240),
                }
            )
        )
    if reviews:
        out["top_reviews"] = reviews
    return out


def extract_product_details(payload: dict[str, Any]) -> dict[str, Any]:
    product = (
        payload.get("product_results")
        if isinstance(payload.get("product_results"), dict)
        else payload
    )
    specs_raw = payload.get("product_details")
    specs = {}
    if isinstance(specs_raw, dict):
        for key, value in specs_raw.items():
            if isinstance(value, str | int | float) and len(specs) < _MAX_SPECS:
                specs[key] = _trim(value, 90)
    out = _clean(
        {
            "name": _trim(product.get("title"), 160),
            "brand": (payload.get("product_details") or {}).get("brand_name")
            if isinstance(payload.get("product_details"), dict)
            else None,
            "asin": product.get("asin"),
            "price": product.get("price"),
            "old_price": product.get("old_price"),
            "discount": product.get("discount"),
            "rating": product.get("rating"),
            "reviews": product.get("reviews"),
            "stock": product.get("stock"),
            "delivery": (product.get("delivery") or [])[:2],
            "link": product.get("link_clean") or product.get("link"),
            "image": product.get("thumbnail"),
            "images": (product.get("thumbnails") or [])[:_MAX_IMAGES],
            "about": [_trim(b, 140) for b in (payload.get("about_item") or [])[:_MAX_BULLETS]],
            "specs": specs or None,
            "description": _trim(payload.get("product_description"), 400) or None,
        }
    )
    out.update(_review_block(payload))
    return out
