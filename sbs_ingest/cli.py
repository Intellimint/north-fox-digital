from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from pathlib import Path
from typing import Any

from .config import DEFAULT_ENDPOINT, DEFAULT_SLEEP_SECONDS, DEFAULT_TIMEOUT, DEFAULT_USER_AGENT, MAX_RETRIES
from .db import db_session, extract_row
from .fetcher import fetch_state_to_cache, write_meta_file
from .logging_utils import configure_logging
from .parser import iter_results_from_gzip
from .states import SUPPORTED_STATE_CODES, normalize_state_code

logger = logging.getLogger(__name__)


def _infer_state_from_file(path: Path) -> str:
    name = path.name
    if name.endswith(".json.gz"):
        code = name[:-8]
    else:
        code = path.stem
    return normalize_state_code(code)


def _import_gzip_into_db(
    db,
    gz_path: Path,
    batch_size: int = 500,
) -> tuple[int, int]:
    total_seen = 0
    total_upserted = 0
    batch: list[dict[str, Any]] = []
    for record in iter_results_from_gzip(gz_path):
        total_seen += 1
        row = extract_row(record)
        if row is None:
            continue
        batch.append(row)
        if len(batch) >= batch_size:
            total_upserted += db.upsert_rows(batch)
            batch.clear()
    if batch:
        total_upserted += db.upsert_rows(batch)
    return total_seen, total_upserted


def _load_meta(meta_path: Path) -> dict[str, Any]:
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def cmd_init_db(args: argparse.Namespace) -> int:
    with db_session(args.db) as db:
        db.init_db()
    logger.info("Initialized database schema for %s", args.db)
    return 0


def _do_fetch_state(args: argparse.Namespace, state_code: str) -> dict[str, Any]:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f"{state_code}.json.gz"
    meta_path = out_dir / f"{state_code}.meta.json"

    with db_session(args.db) as db:
        run = db.start_run(state_code)
        try:
            fetch_info = None
            if args.use_cache and raw_path.exists() and not args.force_download:
                logger.info("Using cached raw file for %s: %s", state_code, raw_path)
                meta = _load_meta(meta_path)
                bytes_downloaded = meta.get("content_length")
                etag = meta.get("etag")
            else:
                import httpx

                with httpx.Client(http2=True) as client:
                    fetch_info = fetch_state_to_cache(
                        client=client,
                        state_code=state_code,
                        out_dir=out_dir,
                        endpoint=args.endpoint,
                        timeout=args.timeout,
                        user_agent=args.user_agent,
                        max_retries=args.max_retries,
                    )
                bytes_downloaded = fetch_info.content_length
                etag = fetch_info.etag

            record_count, upserted = _import_gzip_into_db(db, raw_path)
            if fetch_info is not None:
                metadata = {
                    "pulled_at": fetch_info.pulled_at,
                    "http_status": fetch_info.http_status,
                    "content_length": fetch_info.content_length,
                    "etag": fetch_info.etag,
                    "record_count": record_count,
                    "sha256": fetch_info.sha256_uncompressed,
                }
                write_meta_file(meta_path, metadata)
            else:
                meta = _load_meta(meta_path)
                if meta and "record_count" not in meta:
                    meta["record_count"] = record_count
                    write_meta_file(meta_path, meta)

            db.finish_run(
                run,
                record_count=record_count,
                bytes_downloaded=bytes_downloaded,
                etag=etag,
                status="success",
            )
            logger.info(
                "Completed %s: records=%s upserted=%s raw=%s",
                state_code,
                record_count,
                upserted,
                raw_path,
            )
            return {
                "state": state_code,
                "record_count": record_count,
                "upserted": upserted,
                "bytes_downloaded": bytes_downloaded,
                "status": "success",
            }
        except Exception as exc:
            db.finish_run(
                run,
                record_count=0,
                bytes_downloaded=None,
                etag=None,
                status="fail",
                error_message=str(exc),
            )
            logger.exception("Failed state %s", state_code)
            return {
                "state": state_code,
                "record_count": 0,
                "upserted": 0,
                "bytes_downloaded": None,
                "status": "fail",
                "error": str(exc),
            }


def cmd_fetch_state(args: argparse.Namespace) -> int:
    state_code = normalize_state_code(args.state)
    result = _do_fetch_state(args, state_code)
    return 0 if result["status"] == "success" else 1


def cmd_fetch_all(args: argparse.Namespace) -> int:
    states = [normalize_state_code(s) for s in (args.states or SUPPORTED_STATE_CODES)]
    summaries: list[dict[str, Any]] = []
    for idx, state_code in enumerate(states, start=1):
        logger.info("Progress %s/%s: %s", idx, len(states), state_code)
        summaries.append(_do_fetch_state(args, state_code))
        if idx < len(states):
            delay = args.sleep_seconds
            if args.polite_jitter:
                delay = max(0.0, delay + random.uniform(-1.0, 2.0))
            logger.info("Sleeping %.1fs before next state", delay)
            time.sleep(delay)

    success = sum(1 for s in summaries if s["status"] == "success")
    failures = [s for s in summaries if s["status"] != "success"]
    total_records = sum(int(s.get("record_count") or 0) for s in summaries)
    logger.info(
        "fetch-all complete: success=%s fail=%s total_records=%s",
        success,
        len(failures),
        total_records,
    )
    if failures:
        for item in failures:
            logger.error("Failed %s: %s", item["state"], item.get("error"))
        return 1
    return 0


def cmd_fetch_pending(args: argparse.Namespace) -> int:
    candidate_states = [normalize_state_code(s) for s in (args.states or SUPPORTED_STATE_CODES)]
    with db_session(args.db) as db:
        try:
            latest_status = db.latest_run_statuses()
        except Exception as exc:
            logger.error("Unable to read sbs_ingest_runs. Did you run init-db? Error: %s", exc)
            return 1

    pending = [s for s in candidate_states if latest_status.get(s) != "success"]
    successful = [s for s in candidate_states if latest_status.get(s) == "success"]

    logger.info(
        "Pending geographies: %s (success=%s, pending=%s)",
        len(candidate_states),
        len(successful),
        len(pending),
    )
    if args.print_plan:
        logger.info("Successful: %s", " ".join(successful) if successful else "(none)")
        logger.info("Pending: %s", " ".join(pending) if pending else "(none)")
    if not pending:
        logger.info("No failed/missing geographies to fetch.")
        return 0

    original_states = getattr(args, "states", None)
    args.states = pending
    try:
        return cmd_fetch_all(args)
    finally:
        args.states = original_states


def cmd_import_raw(args: argparse.Namespace) -> int:
    gz_path = Path(args.file)
    state_code = normalize_state_code(args.state) if args.state else _infer_state_from_file(gz_path)
    with db_session(args.db) as db:
        run = db.start_run(state_code)
        try:
            record_count, upserted = _import_gzip_into_db(db, gz_path)
            meta_path = gz_path.with_suffix("").with_suffix(".meta.json")
            meta = _load_meta(meta_path)
            db.finish_run(
                run,
                record_count=record_count,
                bytes_downloaded=meta.get("content_length"),
                etag=meta.get("etag"),
                status="success",
            )
            logger.info(
                "Imported %s: records=%s upserted=%s",
                gz_path,
                record_count,
                upserted,
            )
            return 0
        except Exception as exc:
            db.finish_run(
                run,
                record_count=0,
                bytes_downloaded=None,
                etag=None,
                status="fail",
                error_message=str(exc),
            )
            logger.exception("Import failed for %s", gz_path)
            return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m sbs_ingest.cli")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subp: argparse.ArgumentParser, include_state: bool = False) -> None:
        if include_state:
            subp.add_argument("--state", required=True, help="State/territory code (e.g., FL)")
        subp.add_argument("--db", required=True, help="DB URL (postgres://... or sqlite:///path.db)")
        subp.add_argument("--out", default="data/raw", help="Raw cache directory")
        subp.add_argument("--use-cache", action="store_true", help="Use cached .json.gz if present")
        subp.add_argument("--force-download", action="store_true", help="Ignore cache and redownload")
        subp.add_argument(
            "--sleep-seconds",
            type=float,
            default=DEFAULT_SLEEP_SECONDS,
            help="Delay between states for fetch-all",
        )
        subp.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout seconds")
        subp.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="SBA search API endpoint")
        subp.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="HTTP User-Agent")
        subp.add_argument("--max-retries", type=int, default=MAX_RETRIES, help="Max retry attempts")

    p_init = subparsers.add_parser("init-db", help="Create tables and indexes")
    p_init.add_argument("--db", required=True, help="DB URL (postgres://... or sqlite:///path.db)")
    p_init.set_defaults(func=cmd_init_db)

    p_fetch_state = subparsers.add_parser("fetch-state", help="Fetch a single state and ingest it")
    add_common(p_fetch_state, include_state=True)
    p_fetch_state.set_defaults(func=cmd_fetch_state)

    p_fetch_all = subparsers.add_parser("fetch-all", help="Fetch and ingest all supported states")
    add_common(p_fetch_all, include_state=False)
    p_fetch_all.add_argument(
        "--states",
        nargs="*",
        help="Optional subset of state/territory codes (default: all supported)",
    )
    p_fetch_all.add_argument(
        "--polite-jitter",
        action="store_true",
        help="Add jitter to inter-state delay",
    )
    p_fetch_all.set_defaults(func=cmd_fetch_all)

    p_fetch_pending = subparsers.add_parser(
        "fetch-pending",
        help="Fetch only geographies whose latest ingest run is missing or failed",
    )
    add_common(p_fetch_pending, include_state=False)
    p_fetch_pending.add_argument(
        "--states",
        nargs="*",
        help="Optional subset to evaluate for missing/failed status",
    )
    p_fetch_pending.add_argument(
        "--polite-jitter",
        action="store_true",
        help="Add jitter to inter-state delay",
    )
    p_fetch_pending.add_argument(
        "--print-plan",
        action="store_true",
        help="Log the successful and pending state lists before running",
    )
    p_fetch_pending.set_defaults(func=cmd_fetch_pending)

    p_import = subparsers.add_parser("import-raw", help="Import a cached .json.gz file")
    p_import.add_argument("--db", required=True, help="DB URL (postgres://... or sqlite:///path.db)")
    p_import.add_argument("--file", required=True, help="Path to .json.gz raw cache file")
    p_import.add_argument("--state", help="Override state code if filename is not STATE.json.gz")
    p_import.set_defaults(func=cmd_import_raw)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
