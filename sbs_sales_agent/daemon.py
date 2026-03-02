from __future__ import annotations

import time

from .config import AgentSettings
from .db import OpsDB
from .inbound.poller import poll_agentmail_inbox
from .worker import dispatch_scheduled_messages, reconcile_payments


def run_daemon(
    settings: AgentSettings,
    *,
    poll_every_seconds: int = 60,
    reconcile_every_seconds: int = 900,
) -> None:
    ops_db = OpsDB(settings.ops_db_path)
    ops_db.init_db()
    last_reconcile = 0.0
    while True:
        now = time.time()
        try:
            poll_agentmail_inbox(settings, ops_db, dry_run=False)
        except Exception:
            pass
        try:
            dispatch_scheduled_messages(settings, dry_run=False)
        except Exception:
            pass
        if (now - last_reconcile) >= max(60, int(reconcile_every_seconds)):
            try:
                reconcile_payments(settings, dry_run=False)
            except Exception:
                pass
            last_reconcile = now
        time.sleep(max(10, int(poll_every_seconds)))

