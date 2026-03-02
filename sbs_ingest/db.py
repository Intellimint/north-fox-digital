from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

from .parser import dumps_json

logger = logging.getLogger(__name__)

EXTRACTED_COLUMNS = [
    "entity_detail_id",
    "meili_primary_key",
    "uei",
    "cage_code",
    "legal_business_name",
    "dba_name",
    "contact_person",
    "email",
    "phone",
    "fax",
    "website",
    "additional_website",
    "address_1",
    "address_2",
    "city",
    "state",
    "zipcode",
    "county",
    "msa",
    "congressional_district",
    "naics_primary",
    "description",
    "keywords",
    "tags",
    "certs",
    "last_update_date",
    "display_email",
    "display_phone",
    "public_display",
    "public_display_limited",
    "raw",
]


def _get(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record:
            return record.get(key)
    return None


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"true", "1", "yes", "y"}:
            return True
        if s in {"false", "0", "no", "n"}:
            return False
    return None


def _to_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.isdigit():
            return datetime.fromtimestamp(int(s), tz=timezone.utc)
        try:
            # Preserve ISO strings if already provided.
            parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return None
    return None


def extract_row(record: dict[str, Any]) -> dict[str, Any] | None:
    entity_detail_id = _get(record, "entityDetailId", "entity_detail_id")
    if entity_detail_id in (None, ""):
        return None
    try:
        entity_detail_id = int(entity_detail_id)
    except (TypeError, ValueError):
        return None

    row = {
        "entity_detail_id": entity_detail_id,
        "meili_primary_key": _get(record, "meiliPrimaryKey", "meili_primary_key"),
        "uei": _get(record, "uei", "UEI"),
        "cage_code": _get(record, "cageCode", "cage_code"),
        "legal_business_name": _get(record, "legalBusinessName", "legal_business_name"),
        "dba_name": _get(record, "dbaName", "dba_name"),
        "contact_person": _get(record, "contactPerson", "contact_person"),
        "email": _get(record, "email"),
        "phone": _get(record, "phone"),
        "fax": _get(record, "fax"),
        "website": _get(record, "website"),
        "additional_website": _get(record, "additionalWebsite", "additional_website"),
        "address_1": _get(record, "address1", "address_1"),
        "address_2": _get(record, "address2", "address_2"),
        "city": _get(record, "city"),
        "state": _get(record, "state"),
        "zipcode": _get(record, "zipCode", "zipcode"),
        "county": _get(record, "county"),
        "msa": _get(record, "msa"),
        "congressional_district": _get(record, "congressionalDistrict", "congressional_district"),
        "naics_primary": _get(record, "naicsPrimary", "naics_primary"),
        "description": _get(record, "capabilitiesNarrative", "capabilities_narrative"),
        "keywords": _get(record, "keywords"),
        "tags": _get(record, "meiliSelfCertifications", "meili_self_certifications"),
        "certs": _get(record, "certs"),
        "last_update_date": _to_datetime(_get(record, "lastUpdateDate", "last_update_date")),
        "display_email": _to_bool(_get(record, "displayEmail", "display_email")),
        "display_phone": _to_bool(_get(record, "displayPhone", "display_phone")),
        "public_display": _to_bool(_get(record, "publicDisplay", "public_display")),
        "public_display_limited": _to_bool(
            _get(record, "publicDisplayLimited", "public_display_limited")
        ),
        "raw": record,
    }
    return row


def _normalize_db_url(db_url: str) -> str:
    if db_url.startswith("postgres://"):
        return "postgresql://" + db_url[len("postgres://") :]
    return db_url


@dataclass(slots=True)
class RunHandle:
    id: int


class BaseDB:
    def init_db(self) -> None:
        raise NotImplementedError

    def start_run(self, geography: str) -> RunHandle:
        raise NotImplementedError

    def finish_run(
        self,
        run: RunHandle,
        *,
        record_count: int,
        bytes_downloaded: int | None,
        etag: str | None,
        status: str,
        error_message: str | None = None,
    ) -> None:
        raise NotImplementedError

    def upsert_rows(self, rows: list[dict[str, Any]]) -> int:
        raise NotImplementedError

    def latest_run_statuses(self) -> dict[str, str]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class PostgresDB(BaseDB):
    def __init__(self, db_url: str) -> None:
        if psycopg is None:
            raise RuntimeError("psycopg is required for PostgreSQL support")
        self.conn = psycopg.connect(_normalize_db_url(db_url))
        self.conn.autocommit = False

    def init_db(self) -> None:
        sql_path = Path(__file__).resolve().parent / "migrations" / "postgres.sql"
        self.conn.execute(sql_path.read_text(encoding="utf-8"))
        self.conn.commit()

    def start_run(self, geography: str) -> RunHandle:
        cur = self.conn.execute(
            "INSERT INTO sbs_ingest_runs (run_started_at, geography, status) VALUES (now(), %s, %s) RETURNING id",
            (geography, "running"),
        )
        run_id = int(cur.fetchone()[0])
        self.conn.commit()
        return RunHandle(run_id)

    def finish_run(
        self,
        run: RunHandle,
        *,
        record_count: int,
        bytes_downloaded: int | None,
        etag: str | None,
        status: str,
        error_message: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE sbs_ingest_runs
            SET run_finished_at = now(),
                record_count = %s,
                bytes_downloaded = %s,
                etag = %s,
                status = %s,
                error_message = %s
            WHERE id = %s
            """,
            (record_count, bytes_downloaded, etag, status, error_message, run.id),
        )
        self.conn.commit()

    def upsert_rows(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        sql = """
            INSERT INTO sbs_entities (
                entity_detail_id, meili_primary_key, uei, cage_code, legal_business_name, dba_name,
                contact_person, email, phone, fax, website, additional_website, address_1, address_2,
                city, state, zipcode, county, msa, congressional_district, naics_primary,
                description, keywords, tags, certs,
                last_update_date, display_email, display_phone, public_display, public_display_limited, raw
            )
            VALUES (
                %(entity_detail_id)s, %(meili_primary_key)s, %(uei)s, %(cage_code)s, %(legal_business_name)s, %(dba_name)s,
                %(contact_person)s, %(email)s, %(phone)s, %(fax)s, %(website)s, %(additional_website)s, %(address_1)s, %(address_2)s,
                %(city)s, %(state)s, %(zipcode)s, %(county)s, %(msa)s, %(congressional_district)s, %(naics_primary)s,
                %(description)s, %(keywords_json)s::jsonb, %(tags_json)s::jsonb, %(certs_json)s::jsonb,
                %(last_update_date)s, %(display_email)s, %(display_phone)s, %(public_display)s, %(public_display_limited)s, %(raw_json)s::jsonb
            )
            ON CONFLICT (entity_detail_id) DO UPDATE SET
                meili_primary_key = EXCLUDED.meili_primary_key,
                uei = EXCLUDED.uei,
                cage_code = EXCLUDED.cage_code,
                legal_business_name = EXCLUDED.legal_business_name,
                dba_name = EXCLUDED.dba_name,
                contact_person = EXCLUDED.contact_person,
                email = EXCLUDED.email,
                phone = EXCLUDED.phone,
                fax = EXCLUDED.fax,
                website = EXCLUDED.website,
                additional_website = EXCLUDED.additional_website,
                address_1 = EXCLUDED.address_1,
                address_2 = EXCLUDED.address_2,
                city = EXCLUDED.city,
                state = EXCLUDED.state,
                zipcode = EXCLUDED.zipcode,
                county = EXCLUDED.county,
                msa = EXCLUDED.msa,
                congressional_district = EXCLUDED.congressional_district,
                naics_primary = EXCLUDED.naics_primary,
                description = EXCLUDED.description,
                keywords = EXCLUDED.keywords,
                tags = EXCLUDED.tags,
                certs = EXCLUDED.certs,
                last_update_date = EXCLUDED.last_update_date,
                display_email = EXCLUDED.display_email,
                display_phone = EXCLUDED.display_phone,
                public_display = EXCLUDED.public_display,
                public_display_limited = EXCLUDED.public_display_limited,
                raw = EXCLUDED.raw,
                updated_at = now()
        """
        payload = []
        for row in rows:
            row_copy = dict(row)
            row_copy["raw_json"] = dumps_json(row["raw"])
            row_copy["keywords_json"] = dumps_json(row.get("keywords"))
            row_copy["tags_json"] = dumps_json(row.get("tags"))
            row_copy["certs_json"] = dumps_json(row.get("certs"))
            payload.append(row_copy)
        with self.conn.cursor() as cur:
            cur.executemany(sql, payload)
        self.conn.commit()
        return len(rows)

    def latest_run_statuses(self) -> dict[str, str]:
        cur = self.conn.execute(
            """
            SELECT r.geography, r.status
            FROM sbs_ingest_runs r
            JOIN (
                SELECT geography, MAX(id) AS max_id
                FROM sbs_ingest_runs
                GROUP BY geography
            ) latest ON latest.max_id = r.id
            """
        )
        return {str(geo): str(status) for geo, status in cur.fetchall()}

    def close(self) -> None:
        self.conn.close()


class SQLiteDB(BaseDB):
    def __init__(self, db_url: str) -> None:
        raw_path = db_url[len("sqlite:///") :]
        if not raw_path:
            raise ValueError(f"Invalid sqlite URL: {db_url}")
        db_path = Path(raw_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")

    def init_db(self) -> None:
        sql_path = Path(__file__).resolve().parent / "migrations" / "sqlite.sql"
        self.conn.executescript(sql_path.read_text(encoding="utf-8"))
        self.conn.commit()

    def start_run(self, geography: str) -> RunHandle:
        cur = self.conn.execute(
            "INSERT INTO sbs_ingest_runs (run_started_at, geography, status) VALUES (?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), geography, "running"),
        )
        self.conn.commit()
        return RunHandle(int(cur.lastrowid))

    def finish_run(
        self,
        run: RunHandle,
        *,
        record_count: int,
        bytes_downloaded: int | None,
        etag: str | None,
        status: str,
        error_message: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE sbs_ingest_runs
            SET run_finished_at = ?, record_count = ?, bytes_downloaded = ?, etag = ?, status = ?, error_message = ?
            WHERE id = ?
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                record_count,
                bytes_downloaded,
                etag,
                status,
                error_message,
                run.id,
            ),
        )
        self.conn.commit()

    def upsert_rows(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        sql = """
            INSERT INTO sbs_entities (
                entity_detail_id, meili_primary_key, uei, cage_code, legal_business_name, dba_name,
                contact_person, email, phone, fax, website, additional_website, address_1, address_2,
                city, state, zipcode, county, msa, congressional_district, naics_primary,
                description, keywords, tags, certs,
                last_update_date, display_email, display_phone, public_display, public_display_limited, raw, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_detail_id) DO UPDATE SET
                meili_primary_key=excluded.meili_primary_key,
                uei=excluded.uei,
                cage_code=excluded.cage_code,
                legal_business_name=excluded.legal_business_name,
                dba_name=excluded.dba_name,
                contact_person=excluded.contact_person,
                email=excluded.email,
                phone=excluded.phone,
                fax=excluded.fax,
                website=excluded.website,
                additional_website=excluded.additional_website,
                address_1=excluded.address_1,
                address_2=excluded.address_2,
                city=excluded.city,
                state=excluded.state,
                zipcode=excluded.zipcode,
                county=excluded.county,
                msa=excluded.msa,
                congressional_district=excluded.congressional_district,
                naics_primary=excluded.naics_primary,
                description=excluded.description,
                keywords=excluded.keywords,
                tags=excluded.tags,
                certs=excluded.certs,
                last_update_date=excluded.last_update_date,
                display_email=excluded.display_email,
                display_phone=excluded.display_phone,
                public_display=excluded.public_display,
                public_display_limited=excluded.public_display_limited,
                raw=excluded.raw,
                updated_at=excluded.updated_at
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        values = []
        for row in rows:
            dt = row.get("last_update_date")
            last_update = dt.isoformat() if isinstance(dt, datetime) else None
            values.append(
                (
                    row["entity_detail_id"],
                    row.get("meili_primary_key"),
                    row.get("uei"),
                    row.get("cage_code"),
                    row.get("legal_business_name"),
                    row.get("dba_name"),
                    row.get("contact_person"),
                    row.get("email"),
                    row.get("phone"),
                    row.get("fax"),
                    row.get("website"),
                    row.get("additional_website"),
                    row.get("address_1"),
                    row.get("address_2"),
                    row.get("city"),
                    row.get("state"),
                    row.get("zipcode"),
                    row.get("county"),
                    row.get("msa"),
                    row.get("congressional_district"),
                    row.get("naics_primary"),
                    row.get("description"),
                    dumps_json(row.get("keywords")),
                    dumps_json(row.get("tags")),
                    dumps_json(row.get("certs")),
                    last_update,
                    row.get("display_email"),
                    row.get("display_phone"),
                    row.get("public_display"),
                    row.get("public_display_limited"),
                    dumps_json(row["raw"]),
                    now_iso,
                )
            )
        self.conn.executemany(sql, values)
        self.conn.commit()
        return len(rows)

    def latest_run_statuses(self) -> dict[str, str]:
        cur = self.conn.execute(
            """
            SELECT r.geography, r.status
            FROM sbs_ingest_runs r
            JOIN (
                SELECT geography, MAX(id) AS max_id
                FROM sbs_ingest_runs
                GROUP BY geography
            ) latest ON latest.max_id = r.id
            """
        )
        return {str(row["geography"]): str(row["status"]) for row in cur.fetchall()}

    def close(self) -> None:
        self.conn.close()


def connect_db(db_url: str) -> BaseDB:
    if db_url.startswith(("postgres://", "postgresql://")):
        return PostgresDB(db_url)
    if db_url.startswith("sqlite:///"):
        return SQLiteDB(db_url)
    raise ValueError("Unsupported DB URL. Use postgres://, postgresql://, or sqlite:///path.db")


@contextmanager
def db_session(db_url: str):
    db = connect_db(db_url)
    try:
        yield db
    finally:
        db.close()
