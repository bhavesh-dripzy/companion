from __future__ import annotations

from typing import Any


def reply_uber_cab(*, user_message: str, execution: dict[str, Any] | None, execute_tools: bool) -> str:
    if not execute_tools:
        return "I can help with Uber cab booking once tool execution is enabled."

    execution = execution or {}
    if not execution.get("ok"):
        code = execution.get("error_code")
        link = execution.get("deep_link")
        if code == "needs_location":
            return "I need your current location to book or estimate an Uber. Please allow location access and try again."
        if code == "not_connected":
            tail = f" You can still open Uber here: {link}" if link else ""
            return "Uber is not connected in Companion yet, so I cannot book directly." + tail
        if code == "missing_destination":
            tail = f" Try opening Uber with this link: {link}" if link else ""
            return "I could not resolve the dropoff location for Uber." + tail
        err = execution.get("error") or "Uber request failed."
        tail = f" Uber app link: {link}" if link else ""
        return f"{err}{tail}"

    action = execution.get("action")
    product = execution.get("product") or {}
    product_name = product.get("display_name") or product.get("short_description") or "Uber"
    estimate = execution.get("estimate") or {}
    fare = estimate.get("fare") if isinstance(estimate, dict) else None
    fare_text = ""
    if isinstance(fare, dict):
        display = fare.get("display") or fare.get("value")
        currency = fare.get("currency_code")
        if display:
            fare_text = f" Fare: {display}."
        elif fare.get("value") is not None and currency:
            fare_text = f" Fare: {currency} {fare.get('value')}."
    eta = estimate.get("pickup_estimate") if isinstance(estimate, dict) else None
    eta_text = f" Pickup ETA: {eta} min." if eta is not None else ""

    if action == "estimate":
        link = execution.get("deep_link")
        tail = f" Open in Uber: {link}" if link else ""
        return f"{product_name} estimate is ready.{fare_text}{eta_text}{tail}"

    if action == "cancel":
        return "Uber ride cancellation request completed."

    if action == "status":
        uber = execution.get("uber") or {}
        status = uber.get("status") or "status returned"
        return f"Uber ride status: {status}."

    ride = execution.get("ride") or {}
    request_id = ride.get("request_id") or ride.get("id")
    if request_id:
        return f"Booked {product_name}. Request id: {request_id}.{eta_text}"
    return f"Uber ride request was created for {product_name}.{eta_text}"
