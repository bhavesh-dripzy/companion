from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from app.models.member import Member


class Reminder(models.Model):
    class Type(models.TextChoices):
        MEDICINE = "medicine", "medicine"
        WATER = "water", "water"
        MEAL = "meal", "meal"
        CUSTOM = "custom", "custom"

    class ScheduleType(models.TextChoices):
        FIXED = "fixed", "fixed"
        INTERVAL = "interval", "interval"

    class Status(models.TextChoices):
        ACTIVE = "active", "active"
        PAUSED = "paused", "paused"

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="reminders")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.CUSTOM)
    schedule_type = models.CharField(max_length=20, choices=ScheduleType.choices)
    time = models.TimeField(null=True, blank=True)
    interval_minutes = models.PositiveIntegerField(null=True, blank=True)
    next_trigger = models.DateTimeField()
    is_triggered = models.BooleanField(default=False)
    last_triggered = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    requires_confirmation = models.BooleanField(default=True)
    max_retries = models.PositiveIntegerField(default=3)
    retry_interval_minutes = models.PositiveIntegerField(default=5)
    created_by = models.ForeignKey(
        Member,
        on_delete=models.SET_NULL,
        related_name="created_reminders",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "next_trigger"]),
            models.Index(fields=["member", "status"]),
        ]

    def clean(self) -> None:
        if self.schedule_type == self.ScheduleType.FIXED:
            if self.time is None:
                raise ValidationError("time is required when schedule_type is fixed.")
            if self.interval_minutes is not None:
                raise ValidationError(
                    "interval_minutes must be empty when schedule_type is fixed."
                )
        if self.schedule_type == self.ScheduleType.INTERVAL:
            if self.interval_minutes is None:
                raise ValidationError(
                    "interval_minutes is required when schedule_type is interval."
                )
            if self.time is not None:
                raise ValidationError("time must be empty when schedule_type is interval.")

    def calculate_next_trigger(self, from_time=None):
        reference_time = from_time or timezone.now()
        if self.schedule_type == self.ScheduleType.INTERVAL:
            return reference_time + timedelta(minutes=self.interval_minutes)

        fixed_time = self.time
        next_dt = datetime.combine(reference_time.date(), fixed_time)
        if timezone.is_naive(next_dt):
            next_dt = timezone.make_aware(next_dt, timezone.get_current_timezone())
        if next_dt <= reference_time:
            next_dt = next_dt + timedelta(days=1)
        return next_dt

    def __str__(self) -> str:
        return f"{self.title} - {self.member.name}"
