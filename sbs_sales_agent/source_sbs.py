from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable


class SourceProspectRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def get_prospect(self, entity_detail_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sbs_entities WHERE entity_detail_id = ?",
                (entity_detail_id,),
            ).fetchone()
            return dict(row) if row else None

    def select_candidates(self, *, limit: int, offset: int = 0, states: list[str] | None = None) -> list[dict[str, Any]]:
        clauses = [
            "email IS NOT NULL",
            "TRIM(email) <> ''",
            "display_email = 1",
            "public_display = 1",
        ]
        params: list[Any] = []
        if states:
            placeholders = ",".join(["?"] * len(states))
            clauses.append(f"state IN ({placeholders})")
            params.extend(states)
        sql = f"""
            SELECT *
            FROM sbs_entities
            WHERE {' AND '.join(clauses)}
            ORDER BY entity_detail_id
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def iter_candidates(self, *, batch_size: int = 1000) -> Iterable[list[dict[str, Any]]]:
        offset = 0
        while True:
            batch = self.select_candidates(limit=batch_size, offset=offset)
            if not batch:
                return
            yield batch
            offset += len(batch)
