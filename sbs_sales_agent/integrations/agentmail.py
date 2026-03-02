from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx

from ..config import AgentSettings


def _read_attachment_payload(path: Path) -> dict[str, str]:
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    content_type = "application/octet-stream"
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        content_type = "application/pdf"
    elif suffix in {".txt", ".md", ".csv", ".json", ".html"}:
        content_type = {
            ".txt": "text/plain",
            ".md": "text/markdown",
            ".csv": "text/csv",
            ".json": "application/json",
            ".html": "text/html",
        }[suffix]
    return {"content": encoded, "filename": path.name, "content_type": content_type}


class AgentMailClient:
    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings
        self.client = httpx.Client(timeout=self.settings.request_timeout_seconds)

    def _headers(self) -> dict[str, str]:
        headers = {"accept": "application/json"}
        if self.settings.agentmail_api_key:
            headers["authorization"] = f"Bearer {self.settings.agentmail_api_key}"
        return headers

    @staticmethod
    def _retry_without_thread(resp: httpx.Response) -> bool:
        if resp.status_code not in {400, 404, 409, 422}:
            return False
        try:
            body = (resp.text or "").lower()
        except Exception:
            body = ""
        if not body:
            return resp.status_code in {404, 422}
        markers = ("thread", "parent", "reply", "message")
        return any(m in body for m in markers)

    def list_messages(self, inbox_id: str, limit: int = 100) -> dict[str, Any]:
        url = f"{self.settings.agentmail_base_url.rstrip('/')}/inboxes/{inbox_id}/messages"
        resp = self.client.get(url, headers=self._headers(), params={"limit": limit})
        resp.raise_for_status()
        return resp.json()

    def send_message(
        self,
        *,
        inbox_id: str,
        to: list[str],
        subject: str,
        text: str,
        thread_id: str | None = None,
        attachments: list[Path] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.settings.agentmail_base_url.rstrip('/')}/inboxes/{inbox_id}/messages/send"
        payload: dict[str, Any] = {"to": to, "subject": subject, "text": text}
        if thread_id:
            payload["thread_id"] = thread_id
        if attachments:
            payload["attachments"] = [_read_attachment_payload(Path(p)) for p in attachments if Path(p).exists()]
        resp = self.client.post(
            url,
            headers={**self._headers(), "content-type": "application/json"},
            json=payload,
        )
        if thread_id and self._retry_without_thread(resp):
            payload.pop("thread_id", None)
            resp = self.client.post(
                url,
                headers={**self._headers(), "content-type": "application/json"},
                json=payload,
            )
        resp.raise_for_status()
        return resp.json()

    def reply_message(
        self,
        *,
        inbox_id: str,
        message_id: str,
        text: str,
        attachments: list[Path] | None = None,
    ) -> dict[str, Any]:
        safe_message_id = message_id.strip()
        url = f"{self.settings.agentmail_base_url.rstrip('/')}/inboxes/{inbox_id}/messages/{safe_message_id}/reply"
        payload: dict[str, Any] = {"text": text}
        if attachments:
            payload["attachments"] = [_read_attachment_payload(Path(p)) for p in attachments if Path(p).exists()]
        resp = self.client.post(
            url,
            headers={**self._headers(), "content-type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()
