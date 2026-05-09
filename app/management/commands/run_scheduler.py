import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from app.models import Reminder


class Command(BaseCommand):
    help = "Marks due reminders as triggered in a loop."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=int,
            default=10,
            help="Polling interval in seconds for scheduler loop.",
        )

    def handle(self, *args, **options):
        interval = max(1, options["interval"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Reminder scheduler started. Checking every {interval} second(s)."
            )
        )

        while True:
            now = timezone.now()
            due_reminders = Reminder.objects.filter(
                status=Reminder.Status.ACTIVE,
                is_triggered=False,
                next_trigger__lte=now,
            )
            updated_count = due_reminders.update(is_triggered=True, updated_at=now)

            if updated_count:
                self.stdout.write(
                    f"[{now.isoformat()}] Triggered {updated_count} reminder(s)."
                )

            time.sleep(interval)
