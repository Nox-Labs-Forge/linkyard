"""Stripe HTTP client via stdlib urllib. Never logs the secret key."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

API_BASE = "https://api.stripe.com/v1"
USER_AGENT = "linkyard/0.1.0"

Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, dict[str, Any]]]


class StripeError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"Stripe API {status}: {message}")


def flatten(fields: Any, prefix: str = "") -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    if isinstance(fields, dict):
        for key, value in fields.items():
            path = f"{prefix}[{key}]" if prefix else str(key)
            items.extend(flatten(value, path))
    elif isinstance(fields, (list, tuple)):
        for index, value in enumerate(fields):
            items.extend(flatten(value, f"{prefix}[{index}]"))
    elif fields is None:
        pass
    elif isinstance(fields, bool):
        items.append((prefix, "true" if fields else "false"))
    else:
        items.append((prefix, str(fields)))
    return items


def redact(text: str, secret: str | None) -> str:
    if not secret:
        return text
    if secret in text:
        text = text.replace(secret, _mask(secret))
    return text


def _mask(secret: str) -> str:
    if secret.startswith("sk_live_"):
        return "sk_live_***"
    if secret.startswith("sk_test_"):
        return "sk_test_***"
    if len(secret) > 7:
        return secret[:7] + "***"
    return "***"


def key_mode(secret: str) -> str:
    if secret.startswith("sk_live_"):
        return "live"
    if secret.startswith("sk_test_"):
        return "test"
    return "unknown"


def default_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            payload = json.loads(raw.decode()) if raw else {}
            return int(response.status), payload
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode()) if raw else {}
        except json.JSONDecodeError:
            payload = {"error": {"message": raw.decode(errors="replace")[:500]}}
        return int(exc.code), payload
    except urllib.error.URLError as exc:
        raise StripeError(0, f"network error: {exc.reason}") from None


class StripeClient:
    def __init__(self, secret_key: str, transport: Transport | None = None):
        if not secret_key or not secret_key.strip():
            raise StripeError(0, "STRIPE_SECRET_KEY is empty")
        self._key = secret_key.strip()
        self._transport = transport or default_transport

    def request(
        self,
        method: str,
        path: str,
        fields: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        url = f"{API_BASE}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        body: bytes | None = None
        if method == "GET":
            if fields:
                url = url + "?" + urllib.parse.urlencode(flatten(fields))
        else:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urllib.parse.urlencode(flatten(fields or {})).encode()
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key
        try:
            status, data = self._transport(method, url, headers, body)
        except StripeError as exc:
            raise StripeError(exc.status, redact(exc.message, self._key)) from None
        except Exception as exc:
            raise StripeError(0, redact(str(exc), self._key)) from None
        if status >= 400:
            err = data.get("error") if isinstance(data, dict) else None
            if isinstance(err, dict):
                message = str(err.get("message") or err)
            else:
                message = str(data)[:500]
            raise StripeError(status, redact(message, self._key))
        if not isinstance(data, dict):
            raise StripeError(status, "unexpected Stripe response")
        return data

    def create_product(self, *, name: str, description: str, local_id: str) -> dict[str, Any]:
        return self.request(
            "POST",
            "products",
            {
                "name": name,
                "description": description or None,
                "metadata": {"linkyard_id": local_id},
            },
            idempotency_key=f"linkyard-prod-{local_id}",
        )

    def update_product(self, product_id: str, *, name: str, description: str) -> dict[str, Any]:
        return self.request(
            "POST",
            f"products/{product_id}",
            {"name": name, "description": description or None},
        )

    def create_price(
        self,
        *,
        product_id: str,
        amount_cents: int,
        currency: str,
        local_id: str,
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "prices",
            {
                "product": product_id,
                "unit_amount": amount_cents,
                "currency": currency,
                "metadata": {"linkyard_id": local_id},
            },
            idempotency_key=f"linkyard-price-{local_id}-{currency}-{amount_cents}",
        )

    def archive_price(self, price_id: str) -> dict[str, Any]:
        return self.request("POST", f"prices/{price_id}", {"active": False})

    def create_payment_link(
        self,
        *,
        price_id: str,
        local_id: str,
        after_completion: str | None,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "line_items": [{"price": price_id, "quantity": 1}],
            "metadata": {"linkyard_id": local_id},
        }
        if after_completion:
            fields["after_completion"] = {
                "type": "redirect",
                "redirect": {"url": after_completion},
            }
        return self.request(
            "POST",
            "payment_links",
            fields,
            idempotency_key=f"linkyard-plink-{local_id}-{price_id}",
        )

    def update_payment_link(
        self,
        link_id: str,
        *,
        after_completion: str | None = None,
        active: bool | None = None,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if active is not None:
            fields["active"] = active
        if after_completion is not None:
            if after_completion:
                fields["after_completion"] = {
                    "type": "redirect",
                    "redirect": {"url": after_completion},
                }
            else:
                fields["after_completion"] = {"type": "hosted_confirmation"}
        return self.request("POST", f"payment_links/{link_id}", fields)

    def deactivate_payment_link(self, link_id: str) -> dict[str, Any]:
        return self.update_payment_link(link_id, active=False)
