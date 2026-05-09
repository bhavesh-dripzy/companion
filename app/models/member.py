from django.db import models

from app.models.family import Family


class Member(models.Model):
    class Role(models.TextChoices):
        ELDER = "elder", "elder"
        ADMIN = "admin", "admin"
        CAREGIVER = "caregiver", "caregiver"

    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="members")
    name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=20, unique=True)
    role = models.CharField(max_length=20, choices=Role.choices)
    is_admin = models.BooleanField(default=False)
    summary = models.TextField(blank=True, default="")
    language = models.CharField(max_length=10, default="hi")
    timezone = models.CharField(max_length=64, default="Asia/Kolkata")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.phone_number})"
