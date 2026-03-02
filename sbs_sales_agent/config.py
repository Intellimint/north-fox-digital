from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


@dataclass(slots=True)
class AgentSettings:
    sbs_db_path: Path = Path("tmp/sbs_live.db")
    ops_db_path: Path = Path("tmp/sbs_agent_ops.db")
    logs_dir: Path = Path("logs/agent_runs")
    artifacts_dir: Path = Path("tmp/agent_artifacts")
    report_rnd_db_path: Path = Path("tmp/sbs_report_rnd.db")
    timezone_name: str = "America/New_York"

    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_model: str = "gpt-oss:20b"
    codex_fulfillment_cmd: str = ""
    website_research_timeout_seconds: float = 10.0

    agentmail_base_url: str = "https://api.agentmail.to"
    agentmail_api_key: str = ""
    agentmail_sales_inbox: str = "neilfox@agentmail.to"
    agentmail_precheck_feedback_inbox: str = "jefferywacaster@agentmail.to"
    agentmail_webhook_secret: str = ""
    agentmail_forward_token: str = ""

    local_mail_api_url: str = "http://192.168.100.7:8081"
    local_mail_api_token: str = ""
    local_mail_from: str = "contact@osceola.online"

    square_environment: str = "production"
    square_access_token: str = ""
    square_location_id: str = ""
    square_webhook_signature_key: str = ""
    square_version: str = "2026-01-22"

    sender_name: str = "Neil Fox"
    sender_company_name: str = "North Fox Digital"
    sender_address_footer: str = (
        "North Fox Digital, serving clients nationwide from our office at 4103 Tropical Isle Blvd, Ste 124 Kissimmee, FL 34741"
    )
    unsubscribe_footer: str = "If this email isn't relevant to you, just let me know."

    reply_delay_min_minutes: int = 5
    reply_delay_max_minutes: int = 10
    precheck_hold_min_minutes: int = 15
    precheck_hold_max_minutes: int = 30
    request_timeout_seconds: float = 20.0

    initial_send_start_hour_local: int = 9
    initial_send_end_hour_local: int = 17
    daily_total_initial_cap: int = 600
    daily_offer_cap: int = 300
    per_run_offer_cap: int = 150

    bounce_stop_loss_rate: float = 0.05
    spam_complaint_stop_loss_rate: float = 0.001
    min_positive_reply_rate: float = 0.01
    min_positive_to_paid_rate: float = 0.15

    metric_weight_cash: float = 1.0
    metric_weight_positive_reply: float = 0.25
    metric_weight_reply: float = 0.10
    penalty_unsubscribe: float = 0.75
    penalty_bounce: float = 0.60
    penalty_spam_complaint: float = 1.00
    penalty_negative_reply: float = 0.10

    dry_run_default: bool = True
    test_mode: bool = False
    use_llm_first_touch: bool = True
    webhook_enable_daemon: bool = False
    daemon_poll_every_seconds: int = 60
    daemon_reconcile_every_seconds: int = 900

    @classmethod
    def from_env(cls) -> "AgentSettings":
        s = cls()
        s.sbs_db_path = Path(os.getenv("SBS_AGENT_SBS_DB_PATH", str(s.sbs_db_path)))
        s.ops_db_path = Path(os.getenv("SBS_AGENT_OPS_DB_PATH", str(s.ops_db_path)))
        s.logs_dir = Path(os.getenv("SBS_AGENT_LOGS_DIR", str(s.logs_dir)))
        s.artifacts_dir = Path(os.getenv("SBS_AGENT_ARTIFACTS_DIR", str(s.artifacts_dir)))
        s.report_rnd_db_path = Path(os.getenv("SBS_AGENT_REPORT_RND_DB_PATH", str(s.report_rnd_db_path)))
        s.timezone_name = os.getenv("SBS_AGENT_TIMEZONE", s.timezone_name)

        s.ollama_base_url = os.getenv("SBS_AGENT_OLLAMA_BASE_URL", s.ollama_base_url)
        s.ollama_model = os.getenv("SBS_AGENT_OLLAMA_MODEL", s.ollama_model)
        s.codex_fulfillment_cmd = os.getenv("SBS_AGENT_CODEX_FULFILL_CMD", s.codex_fulfillment_cmd)
        s.website_research_timeout_seconds = _env_float(
            "SBS_AGENT_WEBSITE_RESEARCH_TIMEOUT_SECONDS", s.website_research_timeout_seconds
        )

        s.agentmail_base_url = os.getenv("SBS_AGENT_AGENTMAIL_BASE_URL", s.agentmail_base_url)
        s.agentmail_api_key = os.getenv("SBS_AGENT_AGENTMAIL_API_KEY", s.agentmail_api_key)
        s.agentmail_sales_inbox = os.getenv("SBS_AGENT_AGENTMAIL_SALES_INBOX", s.agentmail_sales_inbox)
        s.agentmail_precheck_feedback_inbox = os.getenv(
            "SBS_AGENT_AGENTMAIL_PRECHECK_INBOX", s.agentmail_precheck_feedback_inbox
        )
        s.agentmail_webhook_secret = os.getenv("SBS_AGENT_AGENTMAIL_WEBHOOK_SECRET", s.agentmail_webhook_secret)
        s.agentmail_forward_token = os.getenv("SBS_AGENT_AGENTMAIL_FORWARD_TOKEN", s.agentmail_forward_token)

        s.local_mail_api_url = os.getenv("SBS_AGENT_LOCAL_MAIL_API_URL", s.local_mail_api_url)
        s.local_mail_api_token = os.getenv("SBS_AGENT_LOCAL_MAIL_API_TOKEN", s.local_mail_api_token)
        s.local_mail_from = os.getenv("SBS_AGENT_LOCAL_MAIL_FROM", s.local_mail_from)

        s.square_environment = os.getenv("SBS_AGENT_SQUARE_ENVIRONMENT", s.square_environment)
        s.square_access_token = os.getenv("SBS_AGENT_SQUARE_ACCESS_TOKEN", s.square_access_token)
        s.square_location_id = os.getenv("SBS_AGENT_SQUARE_LOCATION_ID", s.square_location_id)
        s.square_webhook_signature_key = os.getenv(
            "SBS_AGENT_SQUARE_WEBHOOK_SIGNATURE_KEY", s.square_webhook_signature_key
        )
        s.square_version = os.getenv("SBS_AGENT_SQUARE_VERSION", s.square_version)

        s.sender_name = os.getenv("SBS_AGENT_SENDER_NAME", s.sender_name)
        s.sender_company_name = os.getenv("SBS_AGENT_COMPANY_NAME", s.sender_company_name)
        s.sender_address_footer = os.getenv("SBS_AGENT_COMPANY_ADDRESS", s.sender_address_footer)
        s.unsubscribe_footer = os.getenv("SBS_AGENT_UNSUB_FOOTER", s.unsubscribe_footer)

        s.reply_delay_min_minutes = _env_int("SBS_AGENT_REPLY_DELAY_MIN_MINUTES", s.reply_delay_min_minutes)
        s.reply_delay_max_minutes = _env_int("SBS_AGENT_REPLY_DELAY_MAX_MINUTES", s.reply_delay_max_minutes)
        s.precheck_hold_min_minutes = _env_int("SBS_AGENT_PRECHECK_HOLD_MIN_MINUTES", s.precheck_hold_min_minutes)
        s.precheck_hold_max_minutes = _env_int("SBS_AGENT_PRECHECK_HOLD_MAX_MINUTES", s.precheck_hold_max_minutes)
        s.request_timeout_seconds = _env_float("SBS_AGENT_REQUEST_TIMEOUT_SECONDS", s.request_timeout_seconds)

        s.initial_send_start_hour_local = _env_int("SBS_AGENT_INITIAL_SEND_START_HOUR", s.initial_send_start_hour_local)
        s.initial_send_end_hour_local = _env_int("SBS_AGENT_INITIAL_SEND_END_HOUR", s.initial_send_end_hour_local)
        s.daily_total_initial_cap = _env_int("SBS_AGENT_DAILY_TOTAL_INITIAL_CAP", s.daily_total_initial_cap)
        s.daily_offer_cap = _env_int("SBS_AGENT_DAILY_OFFER_CAP", s.daily_offer_cap)
        s.per_run_offer_cap = _env_int("SBS_AGENT_PER_RUN_OFFER_CAP", s.per_run_offer_cap)

        s.bounce_stop_loss_rate = _env_float("SBS_AGENT_BOUNCE_STOP_LOSS_RATE", s.bounce_stop_loss_rate)
        s.spam_complaint_stop_loss_rate = _env_float("SBS_AGENT_SPAM_STOP_LOSS_RATE", s.spam_complaint_stop_loss_rate)
        s.min_positive_reply_rate = _env_float("SBS_AGENT_MIN_POSITIVE_REPLY_RATE", s.min_positive_reply_rate)
        s.min_positive_to_paid_rate = _env_float("SBS_AGENT_MIN_POSITIVE_TO_PAID_RATE", s.min_positive_to_paid_rate)

        s.metric_weight_cash = _env_float("SBS_AGENT_WEIGHT_CASH", s.metric_weight_cash)
        s.metric_weight_positive_reply = _env_float("SBS_AGENT_WEIGHT_POSITIVE_REPLY", s.metric_weight_positive_reply)
        s.metric_weight_reply = _env_float("SBS_AGENT_WEIGHT_REPLY", s.metric_weight_reply)
        s.penalty_unsubscribe = _env_float("SBS_AGENT_PENALTY_UNSUBSCRIBE", s.penalty_unsubscribe)
        s.penalty_bounce = _env_float("SBS_AGENT_PENALTY_BOUNCE", s.penalty_bounce)
        s.penalty_spam_complaint = _env_float("SBS_AGENT_PENALTY_SPAM_COMPLAINT", s.penalty_spam_complaint)
        s.penalty_negative_reply = _env_float("SBS_AGENT_PENALTY_NEGATIVE_REPLY", s.penalty_negative_reply)

        s.dry_run_default = _env_bool("SBS_AGENT_DRY_RUN_DEFAULT", s.dry_run_default)
        s.test_mode = _env_bool("SBS_AGENT_TEST_MODE", s.test_mode)
        s.use_llm_first_touch = _env_bool("SBS_AGENT_USE_LLM_FIRST_TOUCH", s.use_llm_first_touch)
        s.webhook_enable_daemon = _env_bool("SBS_AGENT_WEBHOOK_ENABLE_DAEMON", s.webhook_enable_daemon)
        s.daemon_poll_every_seconds = _env_int("SBS_AGENT_DAEMON_POLL_EVERY_SECONDS", s.daemon_poll_every_seconds)
        s.daemon_reconcile_every_seconds = _env_int(
            "SBS_AGENT_DAEMON_RECONCILE_EVERY_SECONDS", s.daemon_reconcile_every_seconds
        )
        return s

    def ensure_dirs(self) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.ops_db_path.parent.mkdir(parents=True, exist_ok=True)
