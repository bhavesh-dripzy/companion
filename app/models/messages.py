from django.db import models

from app.models.member import Member


class Message(models.Model):
    class Role(models.TextChoices):
        USER = "user", "user"
        ASSISTANT = "assistant", "assistant"
        SYSTEM = "system", "system"

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=Role.choices)
    text = models.TextField()
    intent = models.CharField(max_length=100, null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["member", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.role}: {self.text[:40]}"
