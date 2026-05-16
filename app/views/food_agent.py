import json
import os

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from app.services.food_llm_router import route_food_query
from app.services.llm_config import resolve_llm_from_env
from app.services.food_response_builder import (
    build_assistant_message_with_llm,
    build_zomato_summary,
)
from app.services.zomato_mcp_executor import run_tool_plan_sync


@csrf_exempt
def food_agent_full(request):
    """
    JSON body:
      - message (str, required)
      - address_id (str, optional) — Zomato saved address; else auto from profile
      - execute_zomato (bool, default true) — run MCP after LLM plan
    """
    if request.method != "POST":
        return JsonResponse(
            {"status": "error", "message": "Only POST is allowed."},
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

    address_id = payload.get("address_id")
    if address_id is not None:
        address_id = str(address_id).strip() or None

    execute_zomato = payload.get("execute_zomato", True)
    if not isinstance(execute_zomato, bool):
        execute_zomato = str(execute_zomato).lower() in ("1", "true", "yes")
    llm_cfg, err = resolve_llm_from_env(os.getenv("FOOD_ROUTER_PROVIDER", ""))
    if err or not llm_cfg:
        return JsonResponse(
            {
                "status": "error",
                "message": err or "LLM not configured.",
            },
            status=503,
        )

    try:
        plan = route_food_query(
            user_message=message,
            api_key=llm_cfg.api_key,
            model=llm_cfg.model,
            provider=llm_cfg.provider,
        )
    except ValueError as e:
        return JsonResponse(
            {"status": "error", "message": str(e), "food_routing_plan": None},
            status=502,
        )
    except Exception as e:
        return JsonResponse(
            {
                "status": "error",
                "message": f"{type(e).__name__}: {e}",
                "food_routing_plan": None,
            },
            status=502,
        )

    out: dict = {
        "status": "success",
        "provider": llm_cfg.provider,
        "food_routing_plan": plan,
        "zomato_summary": None,
        "assistant_message": "",
    }
    if execute_zomato:
        tool_plan = plan.get("tool_plan") or []
        if not isinstance(tool_plan, list):
            tool_plan = []
        zomato_result = run_tool_plan_sync(
            tool_plan,
            address_id=address_id,
        )
        out["zomato_summary"] = build_zomato_summary(zomato_result)
    else:
        out["zomato_summary"] = build_zomato_summary(None)

    out["assistant_message"] = build_assistant_message_with_llm(
        user_message=message,
        plan=plan,
        zomato_summary=out.get("zomato_summary") or {},
        provider=llm_cfg.provider,
        api_key=llm_cfg.api_key,
        model=llm_cfg.model,
    )

    return JsonResponse(out)
