from __future__ import annotations

import argparse
import json

from sbs_sales_agent.config import AgentSettings
from sbs_sales_agent.runner import run_orchestrator
from sbs_sales_agent.worker import process_due_prechecks, send_main_outreach_from_passed_prechecks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", choices=["09", "13"], default="09")
    parser.add_argument("--per-run-offer-cap", type=int, default=5)
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--allow-main-send", action="store_true", help="Required to send main outreach after prechecks.")
    args = parser.parse_args()

    settings = AgentSettings.from_env()
    settings.per_run_offer_cap = args.per_run_offer_cap
    settings.ensure_dirs()

    orch = run_orchestrator(settings, slot=args.slot, dry_run=args.dry_run)
    pre = process_due_prechecks(settings, dry_run=args.dry_run)
    out = {"orchestrator": orch, "prechecks": pre}
    if args.allow_main_send:
        out["main_send"] = send_main_outreach_from_passed_prechecks(settings, run_id=orch["run_id"], dry_run=args.dry_run)
    else:
        out["main_send"] = {"skipped": True, "reason": "allow_main_send flag not provided"}
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
