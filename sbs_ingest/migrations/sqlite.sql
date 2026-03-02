CREATE TABLE IF NOT EXISTS sbs_entities (
    entity_detail_id INTEGER PRIMARY KEY,
    meili_primary_key TEXT NULL,
    uei TEXT NULL,
    cage_code TEXT NULL,
    legal_business_name TEXT NULL,
    dba_name TEXT NULL,
    contact_person TEXT NULL,
    email TEXT NULL,
    phone TEXT NULL,
    fax TEXT NULL,
    website TEXT NULL,
    additional_website TEXT NULL,
    address_1 TEXT NULL,
    address_2 TEXT NULL,
    city TEXT NULL,
    state TEXT NULL,
    zipcode TEXT NULL,
    county TEXT NULL,
    msa TEXT NULL,
    congressional_district TEXT NULL,
    naics_primary TEXT NULL,
    description TEXT NULL,
    keywords TEXT NULL,
    tags TEXT NULL,
    certs TEXT NULL,
    last_update_date TEXT NULL,
    display_email INTEGER NULL,
    display_phone INTEGER NULL,
    public_display INTEGER NULL,
    public_display_limited INTEGER NULL,
    raw TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sbs_state ON sbs_entities (state);
CREATE INDEX IF NOT EXISTS idx_sbs_email ON sbs_entities (email);
CREATE INDEX IF NOT EXISTS idx_sbs_uei ON sbs_entities (uei);
CREATE INDEX IF NOT EXISTS idx_sbs_cage ON sbs_entities (cage_code);
CREATE INDEX IF NOT EXISTS idx_sbs_naics_primary ON sbs_entities (naics_primary);

CREATE TABLE IF NOT EXISTS sbs_ingest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_started_at TEXT NOT NULL,
    run_finished_at TEXT NULL,
    geography TEXT NOT NULL,
    record_count INTEGER NULL,
    bytes_downloaded INTEGER NULL,
    etag TEXT NULL,
    status TEXT NOT NULL,
    error_message TEXT NULL
);
