from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import httpx

from ..config import AgentSettings


class SquareClient:
    def __init__(self, settings: AgentSettings, environment: str | None = None) -> None:
        self.settings = settings
        self.environment = (environment or settings.square_environment).lower()
        self.base_url = "https://connect.squareupsandbox.com" if self.environment == "sandbox" else "https://connect.squareup.com"
        self.client = httpx.Client(timeout=self.settings.request_timeout_seconds)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.square_access_token}",
            "Square-Version": self.settings.square_version,
            "Content-Type": "application/json",
        }

    def create_customer(self, *, email: str, company_name: str) -> dict[str, Any]:
        resp = self.client.post(
            f"{self.base_url}/v2/customers",
            headers=self._headers(),
            json={
                "idempotency_key": str(uuid4()),
                "email_address": email,
                "company_name": company_name,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def create_order(self, *, title: str, amount_cents: int, line_item_name: str | None = None) -> dict[str, Any]:
        resp = self.client.post(
            f"{self.base_url}/v2/orders",
            headers=self._headers(),
            json={
                "idempotency_key": str(uuid4()),
                "order": {
                    "location_id": self.settings.square_location_id,
                    "line_items": [
                        {
                            "name": line_item_name or title,
                            "quantity": "1",
                            "base_price_money": {"amount": amount_cents, "currency": "USD"},
                        }
                    ],
                    "state": "OPEN",
                },
            },
        )
        resp.raise_for_status()
        return resp.json()

    def create_invoice(
        self,
        *,
        order_id: str,
        customer_id: str,
        description: str,
        due_date: str | None = None,
    ) -> dict[str, Any]:
        if due_date is None:
            due_date = (date.today() + timedelta(days=1)).isoformat()
        payload = {
            "idempotency_key": str(uuid4()),
            "invoice": {
                "location_id": self.settings.square_location_id,
                "order_id": order_id,
                "primary_recipient": {"customer_id": customer_id},
                "payment_requests": [{"request_type": "BALANCE", "due_date": due_date}],
                "delivery_method": "EMAIL",
                "description": description,
                "accepted_payment_methods": {
                    "card": True,
                    "square_gift_card": False,
                    "bank_account": True,
                    "buy_now_pay_later": False,
                    "cash_app_pay": False,
                },
            },
        }
        resp = self.client.post(f"{self.base_url}/v2/invoices", headers=self._headers(), json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"square_create_invoice_failed:{resp.status_code}:{resp.text}")
        return resp.json()

    def publish_invoice(self, *, invoice_id: str, version: int) -> dict[str, Any]:
        resp = self.client.post(
            f"{self.base_url}/v2/invoices/{invoice_id}/publish",
            headers=self._headers(),
            json={"idempotency_key": str(uuid4()), "version": version},
        )
        resp.raise_for_status()
        return resp.json()

    def create_and_publish_invoice(
        self,
        *,
        customer_email: str,
        customer_name: str,
        title: str,
        amount_cents: int,
        description: str,
        line_item_name: str | None = None,
        reference: str | None = None,
    ) -> dict[str, Any]:
        customer_payload = self.create_customer(email=customer_email, company_name=customer_name)
        customer_id = (customer_payload.get("customer") or {}).get("id")
        if not customer_id:
            raise RuntimeError("square_customer_create_missing_id")
        order_payload = self.create_order(title=title, amount_cents=amount_cents, line_item_name=line_item_name)
        order_id = (order_payload.get("order") or {}).get("id")
        if not order_id:
            raise RuntimeError("square_order_create_missing_id")
        desc = description if not reference else f"{description} Reference: {reference}."
        invoice_payload = self.create_invoice(order_id=order_id, customer_id=customer_id, description=desc)
        invoice_obj = invoice_payload.get("invoice") or {}
        invoice_id = invoice_obj.get("id")
        invoice_version = invoice_obj.get("version")
        if not invoice_id or invoice_version is None:
            raise RuntimeError("square_invoice_create_missing_id_or_version")
        published = self.publish_invoice(invoice_id=str(invoice_id), version=int(invoice_version))
        return {
            "customer": customer_payload.get("customer"),
            "order": order_payload.get("order"),
            "invoice": (published.get("invoice") or invoice_obj),
        }

    def get_invoice(self, invoice_id: str) -> dict[str, Any]:
        resp = self.client.get(f"{self.base_url}/v2/invoices/{invoice_id}", headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def list_payments_for_order(self, order_id: str) -> dict[str, Any]:
        resp = self.client.get(
            f"{self.base_url}/v2/payments",
            headers=self._headers(),
            params={"order_id": order_id, "limit": 20},
        )
        resp.raise_for_status()
        return resp.json()
