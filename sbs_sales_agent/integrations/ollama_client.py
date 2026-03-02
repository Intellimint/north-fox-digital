from __future__ import annotations

from typing import Any

import httpx

from ..config import AgentSettings


class OllamaClient:
    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings
        self.client = httpx.Client(timeout=self.settings.request_timeout_seconds)

    def chat_json(self, *, system: str, user: str, schema_hint: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "model": self.settings.ollama_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        if schema_hint:
            payload["response_format"] = {"type": "json_object"}
        try:
            resp = self.client.post(f"{self.settings.ollama_base_url.rstrip('/')}/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            if not content:
                return {"ok": False, "reason": "empty_content", "raw": data}
            import json
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return {"ok": False, "reason": "non_json_content", "content": content}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}
