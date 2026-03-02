from __future__ import annotations

from typing import Any

import httpx

from ..config import AgentSettings


class LocalMailApiClient:
    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings
        self.client = httpx.Client(timeout=self.settings.request_timeout_seconds)

    def send(self, *, to: str, subject: str, text: str, from_addr: str | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.settings.local_mail_api_token:
            headers["Authorization"] = f"Bearer {self.settings.local_mail_api_token}"
        payload = {
            "from": from_addr or self.settings.local_mail_from,
            "to": [to],
            "subject": subject,
            "text": text,
        }
        resp = self.client.post(f"{self.settings.local_mail_api_url.rstrip('/')}/send", headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()
