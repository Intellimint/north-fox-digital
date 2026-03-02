PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS prospect_contact_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_entity_detail_id INTEGER NOT NULL,
    email_normalized TEXT NOT NULL,
    contact_name_raw TEXT NULL,
    contact_name_normalized TEXT NULL,
    business_name TEXT NULL,
    website_normalized TEXT NULL,
    state TEXT NULL,
    source_snapshot_json TEXT NOT NULL,
    eligible_flag INTEGER NOT NULL DEFAULT 1,
    eligibility_reason TEXT NULL,
    suppressed_flag INTEGER NOT NULL DEFAULT 0,
    suppressed_reason TEXT NULL,
    suppressed_at TEXT NULL,
    last_initial_outreach_at TEXT NULL,
    next_contact_eligible_at TEXT NULL,
    last_negative_signal_at TEXT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_entity_detail_id, email_normalized)
);

CREATE TABLE IF NOT EXISTS offers (
    offer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_key TEXT NOT NULL UNIQUE,
    offer_type TEXT NOT NULL,
    price_cents INTEGER NOT NULL,
    fulfillment_workflow_key TEXT NOT NULL,
    active_flag INTEGER NOT NULL DEFAULT 1,
    targeting_rules_json TEXT NOT NULL,
    sales_constraints_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS offer_variants (
    variant_id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id INTEGER NOT NULL,
    variant_key TEXT NOT NULL UNIQUE,
    subject_template TEXT NOT NULL,
    body_template TEXT NOT NULL,
    style_tags_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (offer_id) REFERENCES offers (offer_id)
);

CREATE TABLE IF NOT EXISTS campaign_runs (
    run_id TEXT PRIMARY KEY,
    run_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NULL,
    summary_file_path TEXT NULL,
    model_versions_json TEXT NOT NULL,
    decision_log_json TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaign_offer_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    offer_key TEXT NOT NULL,
    planned_sends INTEGER NOT NULL DEFAULT 0,
    planned_prechecks INTEGER NOT NULL DEFAULT 0,
    actual_prechecks_sent INTEGER NOT NULL DEFAULT 0,
    actual_main_sends INTEGER NOT NULL DEFAULT 0,
    positive_replies INTEGER NOT NULL DEFAULT 0,
    paid_count INTEGER NOT NULL DEFAULT 0,
    cash_collected_cents INTEGER NOT NULL DEFAULT 0,
    UNIQUE (run_id, offer_key),
    FOREIGN KEY (run_id) REFERENCES campaign_runs (run_id)
);

CREATE TABLE IF NOT EXISTS prospect_offer_attempts (
    attempt_id TEXT PRIMARY KEY,
    source_entity_detail_id INTEGER NOT NULL,
    email_normalized TEXT NOT NULL,
    offer_key TEXT NOT NULL,
    variant_key TEXT NOT NULL,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    send_window_local_date TEXT NOT NULL,
    cooldown_until TEXT NULL,
    score_json TEXT NOT NULL,
    selection_reasons_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    source_entity_detail_id INTEGER NOT NULL,
    email_normalized TEXT NOT NULL,
    offer_key TEXT NOT NULL,
    attempt_id TEXT NULL,
    agentmail_inbox TEXT NULL,
    latest_intent TEXT NULL,
    conversation_state TEXT NOT NULL,
    is_closed INTEGER NOT NULL DEFAULT 0,
    first_contact_at TEXT NULL,
    last_inbound_at TEXT NULL,
    last_outbound_at TEXT NULL,
    thread_metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS email_messages (
    message_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    direction TEXT NOT NULL,
    mailbox TEXT NOT NULL,
    provider_message_id TEXT NULL,
    provider_thread_id TEXT NULL,
    in_reply_to_provider_message_id TEXT NULL,
    subject TEXT NULL,
    body_text TEXT NOT NULL,
    headers_json TEXT NOT NULL DEFAULT '{}',
    recipient_email TEXT NULL,
    sender_email TEXT NULL,
    attempt_id TEXT NULL,
    conversation_id TEXT NULL,
    sent_at TEXT NULL,
    received_at TEXT NULL,
    delivery_status TEXT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inbound_classifications (
    classification_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    email_message_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    raw_output_json TEXT NOT NULL,
    normalized_output_json TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    latency_ms INTEGER NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    attempt_id TEXT NULL,
    square_customer_id TEXT NULL,
    square_order_id TEXT NULL,
    square_invoice_id TEXT NULL,
    square_invoice_number TEXT NULL,
    square_public_url TEXT NULL,
    amount_cents INTEGER NOT NULL,
    status TEXT NOT NULL,
    invoice_sent_at TEXT NULL,
    paid_at TEXT NULL,
    last_reconciled_at TEXT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fulfillment_jobs (
    job_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    offer_key TEXT NOT NULL,
    status TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    artifacts_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NULL,
    completed_at TEXT NULL,
    delivery_email_message_id TEXT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS survey_responses (
    survey_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    email_message_id TEXT NOT NULL,
    rating_overall INTEGER NULL,
    structured_feedback_json TEXT NOT NULL DEFAULT '{}',
    free_text TEXT NOT NULL,
    parsed_sentiment TEXT NULL,
    improvement_signals_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS suppressions (
    suppression_id TEXT PRIMARY KEY,
    email_normalized TEXT NOT NULL,
    source_entity_detail_id INTEGER NULL,
    reason TEXT NOT NULL,
    source_event_id TEXT NULL,
    permanent_flag INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS learning_summaries (
    summary_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    as_of_time TEXT NOT NULL,
    path TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    top_actions_next_run_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS precheck_jobs (
    precheck_id TEXT PRIMARY KEY,
    source_entity_detail_id INTEGER NOT NULL,
    email_normalized TEXT NOT NULL,
    attempt_id TEXT NULL,
    state TEXT NOT NULL,
    local_message_id TEXT NULL,
    local_queue_id TEXT NULL,
    local_response_json TEXT NOT NULL DEFAULT '{}',
    hold_until TEXT NOT NULL,
    decision TEXT NULL,
    decision_reason TEXT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reward_events (
    reward_id TEXT PRIMARY KEY,
    attempt_id TEXT NULL,
    conversation_id TEXT NULL,
    event_type TEXT NOT NULL,
    value REAL NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS runtime_kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pcs_email ON prospect_contact_state (email_normalized);
CREATE INDEX IF NOT EXISTS idx_attempts_status ON prospect_offer_attempts (status);
CREATE INDEX IF NOT EXISTS idx_precheck_state ON precheck_jobs (state, hold_until);
CREATE INDEX IF NOT EXISTS idx_messages_provider ON email_messages (provider_message_id);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON email_messages (conversation_id, direction);
CREATE INDEX IF NOT EXISTS idx_suppressions_email ON suppressions (email_normalized);
