from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import Family, Member, Reminder


class HealthCheckTests(TestCase):
    def test_health_check_returns_ok(self):
        response = self.client.get(reverse("health-check"))
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {"status": "ok"})


class SendWhatsAppMessageTests(TestCase):
    def test_send_message_returns_whatsapp_url(self):
        response = self.client.post(
            reverse("send-whatsapp-message"),
            data='{"message": "Dawai le aana"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "status": "success",
                "whatsapp_url": "https://wa.me/919773687877?text=Dawai%20le%20aana",
            },
        )


class ReminderApiTests(TestCase):
    def setUp(self):
        family = Family.objects.create(name="Test Family")
        self.member = Member.objects.create(
            family=family,
            name="Usha",
            phone_number="+919999999999",
            role=Member.Role.ELDER,
        )

    def test_pending_reminders_returns_triggered_for_member(self):
        reminder = Reminder.objects.create(
            member=self.member,
            title="Take medicine",
            schedule_type=Reminder.ScheduleType.INTERVAL,
            interval_minutes=120,
            next_trigger=timezone.now() - timedelta(minutes=1),
            is_triggered=True,
        )

        response = self.client.get(
            reverse("pending-reminders"), data={"member_id": self.member.id}
        )
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            [
                {
                    "id": reminder.id,
                    "message": "Take medicine",
                    "description": "",
                    "next_trigger": reminder.next_trigger.isoformat(),
                }
            ],
        )

    def test_respond_yes_resets_trigger_and_updates_next(self):
        reminder = Reminder.objects.create(
            member=self.member,
            title="Drink water",
            schedule_type=Reminder.ScheduleType.INTERVAL,
            interval_minutes=60,
            next_trigger=timezone.now() - timedelta(minutes=1),
            is_triggered=True,
        )

        response = self.client.post(
            reverse("respond-reminder"),
            data=f'{{"reminder_id": {reminder.id}, "response": "yes"}}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        reminder.refresh_from_db()
        self.assertFalse(reminder.is_triggered)
