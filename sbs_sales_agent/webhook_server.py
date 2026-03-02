from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any
from urllib.parse import urlparse

from .config import AgentSettings
from .db import OpsDB
from .daemon import run_daemon
from .inbound.webhook_agentmail import process_agentmail_webhook
from .payments.square_webhooks import process_square_webhook_payload, verify_square_signature


class _WebhookHandler(BaseHTTPRequestHandler):
    settings: AgentSettings
    ops_db: OpsDB

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/v1/webhooks/agentmail":
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            result = process_agentmail_webhook(
                settings=self.settings,
                ops_db=self.ops_db,
                raw_body=raw,
                headers={k.lower(): v for k, v in self.headers.items()},
            )
            status = 200 if result.get("ok") else 401 if result.get("reason") == "invalid_signature" else 400
            self._write_json(status, result)
            return
        if parsed.path != "/v1/webhooks/square":
            self._write_json(404, {"ok": False, "reason": "not_found"})
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError:
            self._write_json(400, {"ok": False, "reason": "invalid_json"})
            return

        sig_key = self.settings.square_webhook_signature_key
        if sig_key:
            signature = self.headers.get("x-square-hmacsha256-signature", "")
            host = self.headers.get("Host", "localhost")
            scheme = self.headers.get("X-Forwarded-Proto", "http")
            url = f"{scheme}://{host}{parsed.path}"
            if not verify_square_signature(url, raw, signature, sig_key):
                self._write_json(401, {"ok": False, "reason": "invalid_signature"})
                return

        result = process_square_webhook_payload(self.ops_db, payload)
        self._write_json(200, result)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._write_json(200, {"ok": True})
            return
        self._write_json(404, {"ok": False, "reason": "not_found"})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


def run_webhook_server(settings: AgentSettings, *, host: str = "0.0.0.0", port: int = 8090) -> None:
    ops_db = OpsDB(settings.ops_db_path)
    ops_db.init_db()
    if settings.webhook_enable_daemon:
        Thread(
            target=run_daemon,
            args=(settings,),
            kwargs={
                "poll_every_seconds": settings.daemon_poll_every_seconds,
                "reconcile_every_seconds": settings.daemon_reconcile_every_seconds,
            },
            daemon=True,
        ).start()
    handler_cls = type(
        "WebhookHandler",
        (_WebhookHandler,),
        {"settings": settings, "ops_db": ops_db},
    )
    server = ThreadingHTTPServer((host, port), handler_cls)
    try:
        server.serve_forever()
    finally:
        server.server_close()
