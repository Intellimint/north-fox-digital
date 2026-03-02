from __future__ import annotations

import json
import shlex
import subprocess
from typing import Any

from ..config import AgentSettings


class CodexFulfillmentClient:
    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings

    def enabled(self) -> bool:
        return bool((self.settings.codex_fulfillment_cmd or "").strip())

    def generate(self, *, task: str, payload: dict[str, Any]) -> dict[str, Any]:
        cmd_raw = (self.settings.codex_fulfillment_cmd or "").strip()
        if not cmd_raw:
            return {"ok": False, "reason": "codex_fulfillment_cmd_not_configured"}
        cmd = shlex.split(cmd_raw)
        req = {"task": task, "payload": payload}
        try:
            proc = subprocess.run(
                cmd,
                input=json.dumps(req),
                text=True,
                capture_output=True,
                timeout=max(30, int(self.settings.request_timeout_seconds * 6)),
                check=False,
            )
        except Exception as exc:
            return {"ok": False, "reason": f"codex_exec_exception:{exc}"}
        if proc.returncode != 0:
            return {
                "ok": False,
                "reason": f"codex_exec_failed:{proc.returncode}",
                "stderr": (proc.stderr or "")[-1000:],
                "stdout": (proc.stdout or "")[-1000:],
            }
        raw = (proc.stdout or "").strip()
        if not raw:
            return {"ok": False, "reason": "codex_empty_output"}
        # Accept either pure JSON or logs ending with JSON object.
        candidate = raw.splitlines()[-1]
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return {"ok": False, "reason": "codex_non_json_output", "stdout": raw[-1000:]}
        if not isinstance(data, dict):
            return {"ok": False, "reason": "codex_json_not_object"}
        data.setdefault("ok", True)
        return data
