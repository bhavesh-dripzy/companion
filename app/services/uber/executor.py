from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from companion_api.google_maps import GeoPoint, geocode_address
from app.services.uber.client import (
    UberError,
    cancel_ride,
    create_ride,
    estimate_ride,
    get_products,
    get_ride_status,
    uber_configured,
    uber_environment,
    uber_status,
)

_PROVIDERS = ["uber"]


def _geo_from_payload(geo: Any) -> GeoPoint | None:
    if isinstance(geo, GeoPoint):
        return geo
    if not isinstance(geo, dict):
        return None
    lat = geo.get("latitude", geo.get("lat"))
    lng = geo.get("longitude", geo.get("lng"))
    if lat is None or lng is None:
        return None
    try:
        return GeoPoint(float(lat), float(lng))
    except (TypeError, ValueError):
        return None


def _deep_link(*, pickup: GeoPoint | None, destination_text: str, destination: GeoPoint | None) -> str:
    params: dict[str, Any] = {"action": "setPickup", "pickup": "my_location"}
    if pickup:
        params["pickup[latitude]"] = pickup.latitude
        params["pickup[longitude]"] = pickup.longitude
    if destination:
        params["dropoff[latitude]"] = destination.latitude
        params["dropoff[longitude]"] = destination.longitude
    if destination_text:
        params["dropoff[nickname]"] = destination_text
    return f"https://m.uber.com/ul/?{urlencode(params)}"


def _pick_product(products: list[dict[str, Any]], product_hint: str | None) -> dict[str, Any] | None:
    if not products:
        return None
    hint = (product_hint or "").lower()
    if hint:
        for product in products:
            name = str(product.get("display_name") or product.get("short_description") or "").lower()
            if hint in name.replace(" ", "") or hint in name:
                return product
    for preferred in ("uber go", "ubergo", "go", "auto"):
        for product in products:
            name = str(product.get("display_name") or "").lower()
            if preferred in name or preferred.replace(" ", "") in name.replace(" ", ""):
                return product
    return products[0]


def execute_uber_cab(*, plan: dict[str, Any], user_id: str, user_geo: dict[str, Any] | GeoPoint | None) -> dict[str, Any]:
    status = uber_status(user_id)
    action = str(plan.get("action") or "book").lower()
    request_id = str(plan.get("request_id") or "").strip()

    if action in {"status", "cancel"}:
        if not request_id:
            return {
                "ok": False,
                "error": "I need the Uber request id to check or cancel that ride.",
                "error_code": "missing_request_id",
                "routed_providers": list(_PROVIDERS),
                "uber_status": status,
            }
        if not status["configured"] or not status["connected"]:
            return {
                "ok": False,
                "error": "Uber is not connected for this user.",
                "error_code": "not_connected",
                "routed_providers": list(_PROVIDERS),
                "uber_status": status,
            }
        try:
            result = get_ride_status(user_id=user_id, request_id=request_id) if action == "status" else cancel_ride(user_id=user_id, request_id=request_id)
            return {"ok": True, "routed_providers": list(_PROVIDERS), "action": action, "uber": result, "uber_status": status}
        except UberError as exc:
            return {"ok": False, "error": str(exc), "error_code": exc.code, "routed_providers": list(_PROVIDERS), "uber_status": status}

    pickup = _geo_from_payload(user_geo)
    destination_text = str(plan.get("destination_text") or "").strip()
    destination = geocode_address(destination_text, region="in") if destination_text else None
    deep_link = _deep_link(pickup=pickup, destination_text=destination_text, destination=destination)

    if pickup is None:
        return {
            "ok": False,
            "error": "I need your current location before I can estimate or book an Uber.",
            "error_code": "needs_location",
            "routed_providers": list(_PROVIDERS),
            "deep_link": deep_link,
            "uber_status": status,
        }
    if destination is None:
        return {
            "ok": False,
            "error": "I could not resolve the Uber dropoff location to coordinates.",
            "error_code": "missing_destination",
            "routed_providers": list(_PROVIDERS),
            "deep_link": deep_link,
            "destination_text": destination_text,
            "uber_status": status,
        }

    if not uber_configured() or not status["connected"]:
        return {
            "ok": False,
            "error": "Uber API/OAuth is not connected yet. Use the Uber link or connect Uber in settings.",
            "error_code": "not_connected",
            "routed_providers": list(_PROVIDERS),
            "deep_link": deep_link,
            "origin": {"latitude": pickup.latitude, "longitude": pickup.longitude},
            "destination": {"latitude": destination.latitude, "longitude": destination.longitude, "text": destination_text},
            "uber_status": status,
        }

    try:
        products_payload = get_products(user_id=user_id, latitude=pickup.latitude, longitude=pickup.longitude)
        products = products_payload.get("products") if isinstance(products_payload, dict) else []
        products = products if isinstance(products, list) else []
        product = _pick_product(products, plan.get("product_hint") if isinstance(plan.get("product_hint"), str) else None)
        if not product or not product.get("product_id"):
            return {
                "ok": False,
                "error": "Uber did not return a usable cab product for this pickup.",
                "error_code": "no_products",
                "routed_providers": list(_PROVIDERS),
                "deep_link": deep_link,
                "products": products,
                "uber_status": status,
            }

        estimate = estimate_ride(
            user_id=user_id,
            product_id=str(product["product_id"]),
            start_latitude=pickup.latitude,
            start_longitude=pickup.longitude,
            end_latitude=destination.latitude,
            end_longitude=destination.longitude,
            seat_count=plan.get("seat_count") if isinstance(plan.get("seat_count"), int) else None,
        )
        if action == "estimate":
            return {
                "ok": True,
                "routed_providers": list(_PROVIDERS),
                "action": action,
                "product": product,
                "estimate": estimate,
                "deep_link": deep_link,
                "environment": uber_environment(),
                "uber_status": status,
            }

        fare = estimate.get("fare") if isinstance(estimate, dict) else None
        fare_id = fare.get("fare_id") if isinstance(fare, dict) else None
        ride = create_ride(
            user_id=user_id,
            product_id=str(product["product_id"]),
            start_latitude=pickup.latitude,
            start_longitude=pickup.longitude,
            end_latitude=destination.latitude,
            end_longitude=destination.longitude,
            fare_id=str(fare_id) if fare_id else None,
            seat_count=plan.get("seat_count") if isinstance(plan.get("seat_count"), int) else None,
        )
        return {
            "ok": True,
            "routed_providers": list(_PROVIDERS),
            "action": action,
            "product": product,
            "estimate": estimate,
            "ride": ride,
            "deep_link": deep_link,
            "environment": uber_environment(),
            "uber_status": status,
        }
    except UberError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_code": exc.code,
            "routed_providers": list(_PROVIDERS),
            "deep_link": deep_link,
            "uber_status": status,
        }
