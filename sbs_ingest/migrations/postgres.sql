CREATE TABLE IF NOT EXISTS sbs_entities (
    entity_detail_id BIGINT PRIMARY KEY,
    meili_primary_key UUID NULL,
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
    keywords JSONB NULL,
    tags JSONB NULL,
    certs JSONB NULL,
    last_update_date TIMESTAMPTZ NULL,
    display_email BOOLEAN NULL,
    display_phone BOOLEAN NULL,
    public_display BOOLEAN NULL,
    public_display_limited BOOLEAN NULL,
    raw JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sbs_state ON sbs_entities (state);
CREATE INDEX IF NOT EXISTS idx_sbs_email ON sbs_entities (email);
CREATE INDEX IF NOT EXISTS idx_sbs_uei ON sbs_entities (uei);
CREATE INDEX IF NOT EXISTS idx_sbs_cage ON sbs_entities (cage_code);
CREATE INDEX IF NOT EXISTS idx_sbs_naics_primary ON sbs_entities (naics_primary);
CREATE INDEX IF NOT EXISTS idx_sbs_tags_gin ON sbs_entities USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_sbs_keywords_gin ON sbs_entities USING GIN (keywords);
CREATE INDEX IF NOT EXISTS idx_sbs_raw_gin ON sbs_entities USING GIN (raw);

CREATE TABLE IF NOT EXISTS sbs_ingest_runs (
    id BIGSERIAL PRIMARY KEY,
    run_started_at TIMESTAMPTZ NOT NULL,
    run_finished_at TIMESTAMPTZ NULL,
    geography TEXT NOT NULL,
    record_count INTEGER NULL,
    bytes_downloaded BIGINT NULL,
    etag TEXT NULL,
    status TEXT NOT NULL,
    error_message TEXT NULL
);
