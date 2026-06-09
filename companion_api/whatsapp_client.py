"""WhatsApp Cloud API client (outbound text + template messages).

Self-contained HTTP client over the Meta Graph API. Relocated from
`app/services/whatsapp/client.py` in the legacy teardown; used by the inbound
webhook handler, the scheduled-reminder Lambda, and the pipeline WhatsApp
provider's outbound send.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from companion_api import settings
from companion_api.secrets import secret_or_env

logger = logging.getLogger(__name__)


class WhatsAppClient:
    def __init__(self):
        self.access_token = secret_or_env("WHATSAPP_ACCESS_TOKEN", settings.WHATSAPP_ACCESS_TOKEN)
        self.phone_number_id = secret_or_env(
            "WHATSAPP_PHONE_NUMBER_ID", settings.WHATSAPP_PHONE_NUMBER_ID
        )
        self.version = settings.WHATSAPP_API_VERSION
        self.base_url = f"https://graph.facebook.com/{self.version}/{self.phone_number_id}"

    def send_text_message(self, to: str, text: str) -> dict[str, Any]:
        """Send a standard text message to a WhatsApp number."""
        if not self.access_token or not self.phone_number_id:
            logger.error("WhatsApp credentials missing.")
            return {"error": "credentials_missing"}

        url = f"{self.base_url}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.exception("Failed to send WhatsApp message")
            return {"error": str(e)}

    def send_template_message(
        self,
        to: str,
        template_name: str,
        language_code: str = "en_US",
        components: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Send a template message (required to initiate conversations outside the 24h window)."""
        if not self.access_token or not self.phone_number_id:
            return {"error": "credentials_missing"}

        url = f"{self.base_url}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
            },
        }
        if components:
            payload["template"]["components"] = components

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.exception("Failed to send WhatsApp template")
            return {"error": str(e)}
