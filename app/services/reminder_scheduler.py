from django.db import transaction
from django.utils import timezone

from app.models import Reminder


def trigger_due_reminders_once(now=None) -> int:
    """
    Mark due reminders as triggered once.

    This function is intentionally stateless so it can run from a Django
    management command, an EventBridge-scheduled Lambda, or a test.
    """
    current_time = now or timezone.now()
    with transaction.atomic():
        return (
            Reminder.objects.filter(
                status=Reminder.Status.ACTIVE,
                is_triggered=False,
                next_trigger__lte=current_time,
            ).update(is_triggered=True, updated_at=current_time)
        )
