import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from app.services.reminder_scheduler import trigger_due_reminders_once


class Command(BaseCommand):
    help = "Marks due reminders as triggered in a loop."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=int,
            default=10,
            help="Polling interval in seconds for scheduler loop.",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run one scheduler tick and exit. Use this for EventBridge/Lambda.",
        )

    def handle(self, *args, **options):
        def run_tick() -> None:
            now = timezone.now()
            updated_count = trigger_due_reminders_once(now=now)
            if updated_count:
                self.stdout.write(
                    f"[{now.isoformat()}] Triggered {updated_count} reminder(s)."
                )

        if options["once"]:
            run_tick()
            return

        interval = max(1, options["interval"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Reminder scheduler started. Checking every {interval} second(s)."
            )
        )

        while True:
            run_tick()
            time.sleep(interval)
