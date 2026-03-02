from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_ENDPOINT = "https://search.certifications.sba.gov/_api/v2/search"
DEFAULT_ORIGIN = "https://search.certifications.sba.gov"
DEFAULT_REFERER = "https://search.certifications.sba.gov/advanced?page=0"
DEFAULT_USER_AGENT = "sbs-ingest/0.1 (+https://search.certifications.sba.gov; polite)"
DEFAULT_TIMEOUT = 120.0
DEFAULT_SLEEP_SECONDS = 3.0
MAX_RETRIES = 7
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass(slots=True)
class FetchConfig:
    endpoint: str = DEFAULT_ENDPOINT
    timeout: float = DEFAULT_TIMEOUT
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS
    max_retries: int = MAX_RETRIES
    user_agent: str = DEFAULT_USER_AGENT


@dataclass(slots=True)
class PathsConfig:
    raw_dir: Path = Path("data/raw")
    logs_dir: Path = Path("logs")

