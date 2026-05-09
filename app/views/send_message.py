import json
from urllib.parse import quote

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt


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

    # Hardcoded target number for MVP. Uses India country code.
    whatsapp_number = "919773687877"
    whatsapp_url = f"https://wa.me/{whatsapp_number}?text={quote(message)}"

    return JsonResponse({"status": "success", "whatsapp_url": whatsapp_url})
