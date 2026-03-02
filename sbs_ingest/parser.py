from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

try:
    import orjson  # type: ignore
except ImportError:  # pragma: no cover
    orjson = None


def iter_results_from_gzip(path: str | Path) -> Iterator[dict[str, Any]]:
    import ijson

    with gzip.open(path, "rb") as fh:
        for item in ijson.items(fh, "results.item"):
            if isinstance(item, dict):
                yield item


def dumps_json(value: Any) -> str:
    if orjson is not None:
        return orjson.dumps(value).decode("utf-8")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
