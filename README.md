# SBS Ingest

Command-line tool to download SBA Small Business Search (SBS) results by state/territory, cache raw responses as gzipped JSON, and load records into PostgreSQL (JSONB) or SQLite.

## Features

- Polite single-threaded fetches with retries/backoff
- Raw cache files in `data/raw/{STATE}.json.gz` plus metadata JSON
- Streaming import from gzip via `ijson` (avoids loading large responses into RAM)
- Upsert by `entity_detail_id`
- PostgreSQL and SQLite support

## Install

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Optional faster JSON serialization:

```bash
pip install -e '.[speed]'
```

## Usage

Initialize schema:

```bash
python -m sbs_ingest.cli init-db --db sqlite:///sbs.db
```

Fetch one state (download + import):

```bash
python -m sbs_ingest.cli fetch-state --state FL --db sqlite:///sbs.db --use-cache
```

Fetch all supported geographies sequentially:

```bash
python -m sbs_ingest.cli fetch-all --db postgres://user:pass@localhost:5432/sbs --sleep-seconds 3 --polite-jitter
```

Import a cached raw file without network:

```bash
python -m sbs_ingest.cli import-raw --db sqlite:///sbs.db --file data/raw/FL.json.gz
```

## Notes

- The explicit supported geography list includes `FL` because Florida is referenced in the spec examples and acceptance criteria.
- Raw cache metadata includes `pulled_at`, `http_status`, `content_length`, `etag`, `record_count`, and `sha256`.
- `entity_detail_id` is required for insertion; records missing it are skipped.
