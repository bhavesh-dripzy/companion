import json
from datetime import timedelta

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from app.models import Reminder


@require_GET
def pending_reminders(request):
    member_id = request.GET.get("member_id")
    if not member_id:
        return JsonResponse(
            {"status": "error", "message": "Query parameter 'member_id' is required."},
            status=400,
        )

    reminders = Reminder.objects.filter(
        member_id=member_id,
        status=Reminder.Status.ACTIVE,
        is_triggered=True,
    ).order_by("next_trigger")

    payload = [
        {
            "id": reminder.id,
            "message": reminder.title,
            "description": reminder.description,
            "next_trigger": reminder.next_trigger.isoformat(),
        }
        for reminder in reminders
    ]
    return JsonResponse(payload, safe=False)


@csrf_exempt
def respond_reminder(request):
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

    reminder_id = payload.get("reminder_id")
    response = str(payload.get("response", "")).strip().lower()

    if not reminder_id:
        return JsonResponse(
            {"status": "error", "message": "Field 'reminder_id' is required."},
            status=400,
        )

    if response not in {"yes", "no"}:
        return JsonResponse(
            {"status": "error", "message": "Field 'response' must be 'yes' or 'no'."},
            status=400,
        )

    try:
        reminder = Reminder.objects.get(id=reminder_id)
    except Reminder.DoesNotExist:
        return JsonResponse(
            {"status": "error", "message": "Reminder not found."},
            status=404,
        )

    now = timezone.now()
    reminder.last_triggered = now

    if response == "yes":
        reminder.is_triggered = False
        reminder.next_trigger = reminder.calculate_next_trigger(from_time=now)
    else:
        reminder.next_trigger = now + timedelta(minutes=reminder.retry_interval_minutes)
        reminder.is_triggered = False

    reminder.save(
        update_fields=["is_triggered", "last_triggered", "next_trigger", "updated_at"]
    )
    return JsonResponse({"status": "success", "reminder_id": reminder.id})
