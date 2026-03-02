from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import AgentSettings


def write_run_summary(
    *,
    settings: AgentSettings,
    run_id: str,
    slot: str,
    started_at: datetime,
    metrics: dict[str, Any],
    decisions: dict[str, Any],
) -> Path:
    settings.ensure_dirs()
    stamp = started_at.strftime("%Y-%m-%d_%H%M")
    path = settings.logs_dir / f"{stamp}{settings.timezone_name.replace('/','_')}_{slot}_summary.json"
    payload = {
        "run_id": run_id,
        "slot": slot,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.utcnow().isoformat() + "Z",
        "metrics": metrics,
        "decisions": decisions,
        "next_run_improvements": decisions.get("next_run_improvements", []),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
