from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from email_validator import EmailNotValidError, validate_email


@dataclass(slots=True)
class VerificationResult:
    ok: bool
    decision: str
    reason: str
    normalized_email: str | None
    details: dict[str, Any]


class EmailVerificationClient:
    """Local pre-send verification using email-validator.

    This validates syntax and DNS deliverability (MX/A) before outbound send.
    It does not guarantee inbox placement and cannot fully prevent soft failures.
    """

    def verify(self, email: str) -> VerificationResult:
        try:
            v = validate_email(email, check_deliverability=True)
            details = {
                "ascii_email": v.ascii_email,
                "normalized": v.normalized,
                "domain": v.domain,
            }
            return VerificationResult(
                ok=True,
                decision="safe_to_send_main",
                reason="syntax_and_dns_ok",
                normalized_email=v.normalized,
                details=details,
            )
        except EmailNotValidError as exc:
            return VerificationResult(
                ok=False,
                decision="suppress",
                reason="invalid_or_undeliverable_domain",
                normalized_email=None,
                details={"error": str(exc)},
            )

