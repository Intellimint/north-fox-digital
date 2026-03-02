from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import AgentSettings
from .db import OpsDB
from .daemon import run_daemon
from .features import features_from_sbs_row
from .inbound.poller import poll_agentmail_inbox
from .payments.square_webhooks import process_square_webhook_payload, verify_square_signature
from .research_loop.scan_pipeline import run_scan_pipeline
from .runner import bootstrap_offers, run_orchestrator
from .research_loop.runner import run_loop as run_report_rnd_loop
from .research_loop.runner import run_single_iteration as run_report_rnd_iteration
from .research_loop.runner import summarize_run_date as summarize_report_rnd
from .research_loop.accuracy_audit import run_accuracy_audit
from .source_sbs import SourceProspectRepository
from .webhook_server import run_webhook_server
from .worker import (
    dispatch_scheduled_messages,
    process_due_prechecks,
    reconcile_payments,
    run_fulfillment_jobs,
    send_fulfillment_and_survey,
    send_main_outreach_from_passed_prechecks,
)


def _settings() -> AgentSettings:
    s = AgentSettings.from_env()
    s.ensure_dirs()
    return s


def cmd_init_ops_db(_: argparse.Namespace) -> int:
    settings = _settings()
    db = OpsDB(settings.ops_db_path)
    db.init_db()
    bootstrap_offers(db)
    print(json.dumps({"ok": True, "ops_db_path": str(settings.ops_db_path)}))
    return 0


def cmd_run_orchestrator(args: argparse.Namespace) -> int:
    settings = _settings()
    result = run_orchestrator(settings, slot=args.slot, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


def cmd_process_prechecks(args: argparse.Namespace) -> int:
    settings = _settings()
    result = process_due_prechecks(settings, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


def cmd_send_passed(args: argparse.Namespace) -> int:
    settings = _settings()
    result = send_main_outreach_from_passed_prechecks(settings, run_id=args.run_id, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


def cmd_poll_agentmail(args: argparse.Namespace) -> int:
    settings = _settings()
    result = poll_agentmail_inbox(settings, OpsDB(settings.ops_db_path), dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    settings = _settings()
    result = dispatch_scheduled_messages(settings, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


def cmd_reconcile_square(args: argparse.Namespace) -> int:
    settings = _settings()
    result = reconcile_payments(settings, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


def cmd_run_fulfillment(_: argparse.Namespace) -> int:
    settings = _settings()
    result = run_fulfillment_jobs(settings)
    print(json.dumps(result, indent=2))
    return 0


def cmd_send_fulfillment_and_survey(args: argparse.Namespace) -> int:
    settings = _settings()
    result = send_fulfillment_and_survey(settings, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


def cmd_run_report_rnd_loop(args: argparse.Namespace) -> int:
    settings = _settings()
    result = run_report_rnd_loop(
        settings=settings,
        duration_hours=float(args.duration_hours),
        interval_minutes=int(args.interval_minutes),
        dry_run_email_sim=bool(args.dry_run_email_sim),
    )
    print(json.dumps(result, indent=2))
    return 0


def cmd_run_report_rnd_iteration(args: argparse.Namespace) -> int:
    settings = _settings()
    result = run_report_rnd_iteration(
        settings=settings,
        iteration_id=args.iteration_id,
        entity_id=args.entity_id,
    )
    print(json.dumps(result, indent=2))
    return 0


def cmd_summarize_report_rnd(args: argparse.Namespace) -> int:
    settings = _settings()
    result = summarize_report_rnd(settings=settings, date=args.date)
    print(json.dumps(result, indent=2))
    return 0


def cmd_run_light_scan(args: argparse.Namespace) -> int:
    settings = _settings()
    src = SourceProspectRepository(settings.sbs_db_path)
    row = src.get_prospect(int(args.entity_id))
    if row is None:
        print(json.dumps({"ok": False, "error": f"entity_not_found:{args.entity_id}"}))
        return 2
    feat = features_from_sbs_row(row)
    if not feat.website:
        print(json.dumps({"ok": False, "error": "entity_missing_website"}))
        return 2
    out_dir = Path("logs/light_scans") / time.strftime("%Y-%m-%d") / f"entity_{feat.entity_detail_id}"
    t0 = time.monotonic()
    scan = run_scan_pipeline(settings=settings, website=str(feat.website), out_dir=out_dir, mode="light")
    elapsed = round(time.monotonic() - t0, 2)
    findings = scan.get("findings") or []
    summary = []
    for f in findings[:8]:
        summary.append(
            {
                "category": f.category,
                "severity": f.severity,
                "title": f.title,
            }
        )
    print(
        json.dumps(
            {
                "ok": True,
                "entity_id": feat.entity_detail_id,
                "business_name": feat.business_name,
                "website": feat.website,
                "elapsed_seconds": elapsed,
                "findings_count": len(findings),
                "top_findings": summary,
                "artifact_dir": str(out_dir),
            },
            indent=2,
        )
    )
    return 0


def cmd_run_daemon(args: argparse.Namespace) -> int:
    settings = _settings()
    run_daemon(
        settings,
        poll_every_seconds=int(args.poll_every_seconds),
        reconcile_every_seconds=int(args.reconcile_every_seconds),
    )
    return 0


def cmd_run_accuracy_audit(args: argparse.Namespace) -> int:
    settings = _settings()
    result = run_accuracy_audit(
        settings=settings,
        sample_size=int(args.sample_size),
        deep_count=int(args.deep_count),
    )
    print(json.dumps(result, indent=2))
    return 0


def cmd_process_square_webhook(args: argparse.Namespace) -> int:
    settings = _settings()
    raw_body = sys.stdin.read()
    payload = json.loads(raw_body or "{}")
    if args.verify and settings.square_webhook_signature_key:
        signature = args.signature or ""
        ok = verify_square_signature(args.url, raw_body, signature, settings.square_webhook_signature_key)
        if not ok:
            print(json.dumps({"ok": False, "reason": "invalid_signature"}))
            return 2
    result = process_square_webhook_payload(OpsDB(settings.ops_db_path), payload)
    print(json.dumps(result, indent=2))
    return 0


def cmd_backfill_source_cache(args: argparse.Namespace) -> int:
    settings = _settings()
    db = OpsDB(settings.ops_db_path)
    db.init_db()
    from .source_sbs import SourceProspectRepository
    from .features import features_from_sbs_row, is_valid_email, normalize_email, normalize_website, prospect_snapshot
    src = SourceProspectRepository(settings.sbs_db_path)
    processed = 0
    for batch in src.iter_candidates(batch_size=args.batch_size):
        for row in batch:
            feat = features_from_sbs_row(row)
            if not is_valid_email(feat.email):
                continue
            email = normalize_email(feat.email)
            if not email:
                continue
            db.upsert_prospect_state(
                {
                    "source_entity_detail_id": feat.entity_detail_id,
                    "email_normalized": email,
                    "contact_name_raw": feat.contact_name_raw,
                    "contact_name_normalized": feat.contact_name_normalized,
                    "business_name": feat.business_name,
                    "website_normalized": normalize_website(feat.website),
                    "state": feat.state,
                    "source_snapshot_json": prospect_snapshot(feat),
                    "eligible_flag": True,
                    "eligibility_reason": "backfill",
                }
            )
            processed += 1
            if args.limit and processed >= args.limit:
                print(json.dumps({"ok": True, "processed": processed}))
                return 0
    print(json.dumps({"ok": True, "processed": processed}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sbs-sales-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-ops-db")
    p.set_defaults(func=cmd_init_ops_db)

    p = sub.add_parser("run-orchestrator")
    p.add_argument("--slot", choices=["09", "13"], required=True)
    p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=None)
    p.set_defaults(func=cmd_run_orchestrator)

    p = sub.add_parser("process-prechecks")
    p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=None)
    p.set_defaults(func=cmd_process_prechecks)

    p = sub.add_parser("send-passed-prechecks")
    p.add_argument("--run-id", required=True)
    p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=None)
    p.set_defaults(func=cmd_send_passed)

    p = sub.add_parser("poll-agentmail")
    p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=None)
    p.set_defaults(func=cmd_poll_agentmail)

    p = sub.add_parser("dispatch")
    p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=None)
    p.set_defaults(func=cmd_dispatch)

    p = sub.add_parser("reconcile-square")
    p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=None)
    p.set_defaults(func=cmd_reconcile_square)

    p = sub.add_parser("process-square-webhook")
    p.add_argument("--url", default="https://example.com/v1/webhooks/square")
    p.add_argument("--signature", default="")
    p.add_argument("--verify", action=argparse.BooleanOptionalAction, default=False)
    p.set_defaults(func=cmd_process_square_webhook)

    p = sub.add_parser("backfill-source-cache")
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_backfill_source_cache)

    p = sub.add_parser("run-fulfillment")
    p.set_defaults(func=cmd_run_fulfillment)

    p = sub.add_parser("send-fulfillment-and-survey")
    p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=None)
    p.set_defaults(func=cmd_send_fulfillment_and_survey)

    p = sub.add_parser("run-webhook-server")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8090)
    p.set_defaults(func=lambda args: (run_webhook_server(_settings(), host=args.host, port=args.port), 0)[1])

    p = sub.add_parser("run-report-rnd-loop")
    p.add_argument("--duration-hours", type=float, default=8.0)
    p.add_argument("--interval-minutes", type=int, default=15)
    p.add_argument("--dry-run-email-sim", action=argparse.BooleanOptionalAction, default=True)
    p.set_defaults(func=cmd_run_report_rnd_loop)

    p = sub.add_parser("run-report-rnd-iteration")
    p.add_argument("--iteration-id", default=None)
    p.add_argument("--entity-id", type=int, default=None)
    p.set_defaults(func=cmd_run_report_rnd_iteration)

    p = sub.add_parser("summarize-report-rnd")
    p.add_argument("--date", required=True, help="UTC date in YYYY-MM-DD")
    p.set_defaults(func=cmd_summarize_report_rnd)

    p = sub.add_parser("run-light-scan")
    p.add_argument("--entity-id", type=int, required=True)
    p.set_defaults(func=cmd_run_light_scan)

    p = sub.add_parser("run-daemon")
    p.add_argument("--poll-every-seconds", type=int, default=60)
    p.add_argument("--reconcile-every-seconds", type=int, default=900)
    p.set_defaults(func=cmd_run_daemon)

    p = sub.add_parser("run-accuracy-audit")
    p.add_argument("--sample-size", type=int, default=8)
    p.add_argument("--deep-count", type=int, default=3)
    p.set_defaults(func=cmd_run_accuracy_audit)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "dry_run") and args.dry_run is None:
        args.dry_run = _settings().dry_run_default
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
