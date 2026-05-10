import json
import os
from urllib.parse import quote

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from app.services.food_llm_router import route_food_query
from app.services.llm_config import resolve_llm_from_env


@csrf_exempt
def send_whatsapp_message(request):
    if request.method != "POST":
        return JsonResponse(
            {"status": "error", "message": "Only POST method is allowed."},
            status=405,
        )

    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse(
            {"status": "error", "message": "Invalid JSON body."},
            status=400,
        )

    message = str(payload.get("message", "")).strip()
    if not message:
        return JsonResponse(
            {"status": "error", "message": "Field 'message' is required."},
            status=400,
        )

    force = os.getenv("FOOD_ROUTER_PROVIDER", "").strip().lower()

    whatsapp_number = os.getenv("COMPANION_WHATSAPP_CC_NUMBER", "919773687877")
    whatsapp_url = f"https://wa.me/{whatsapp_number}?text={quote(message)}"

    llm_optional = payload.get("llm_optional") is True

    llm_cfg, err_detail = resolve_llm_from_env(force)

    if err_detail:
        llm_payload = {
            "status": "skipped" if llm_optional else "error",
            "reason": err_detail,
        }
    elif not llm_cfg:
        llm_payload = {
            "status": "skipped" if llm_optional else "error",
            "reason": "Could not select an LLM provider.",
        }
    else:
        try:
            llm_payload = {
                "status": "success",
                "provider": llm_cfg.provider,
                "food_routing_plan": route_food_query(
                    user_message=message,
                    api_key=llm_cfg.api_key,
                    model=llm_cfg.model,
                    provider=llm_cfg.provider,
                ),
            }
        except ValueError as e:
            llm_payload = {"status": "error", "reason": str(e)}
        except Exception as e:
            llm_payload = {"status": "error", "reason": f"{type(e).__name__}: {e}"}

    if llm_payload["status"] == "success":
        body_status = "success"
        status_code = 200
    elif llm_optional:
        body_status = "success"
        status_code = 200
    else:
        body_status = "partial"
        status_code = 503

    response_body = {
        "status": body_status,
        "whatsapp_url": whatsapp_url if payload.get("include_whatsapp_link", True) else None,
        "llm": llm_payload,
    }
    if response_body["whatsapp_url"] is None:
        response_body.pop("whatsapp_url")

    return JsonResponse(response_body, status=status_code)
