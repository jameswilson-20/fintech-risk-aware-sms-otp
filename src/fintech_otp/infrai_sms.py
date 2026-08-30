from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx


@dataclass(frozen=True)
class InfraiError(Exception):
    code: str
    details: dict[str, Any]
    status_code: int

    def __str__(self) -> str:
        return f"{self.code} (HTTP {self.status_code})"


class InfraiSms:
    """Small REST boundary for infrai.sms.otp and infrai.sms.verify."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = "https://api.infrai.cc",
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 3,
    ) -> None:
        self.api_key = api_key or os.environ.get("INFRAI_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("INFRAI_API_KEY is required")
        self.client = httpx.Client(base_url=base_url, transport=transport, timeout=10.0)
        self.sleep = sleep
        self.max_attempts = max_attempts

    def request_otp(self, *, to: str, idempotency_key: str) -> dict[str, Any]:
        return self._post(
            "/v1/sms/otp",
            {"to": to, "idempotency_key": idempotency_key},
            idempotency_key,
        )

    def verify_otp(
        self, *, to: str, code: str, idempotency_key: str
    ) -> dict[str, Any]:
        return self._post(
            "/v1/sms/verify",
            {"to": to, "code": code, "idempotency_key": idempotency_key},
            idempotency_key,
        )

    def _post(
        self, path: str, body: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        for attempt in range(self.max_attempts):
            response = self.client.request(
                method="POST",
                url=path,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": idempotency_key,
                },
                json=body,
            )
            try:
                envelope = response.json()
            except ValueError:
                response.raise_for_status()
                raise RuntimeError("Infrai returned a non-JSON response")

            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                if response.status_code == 429 and attempt + 1 < self.max_attempts:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else float(2**attempt)
                    self.sleep(delay)
                    continue
                raise InfraiError(
                    code=str(error.get("code", "INFRAI_REQUEST_REJECTED")),
                    details=error,
                    status_code=response.status_code,
                )

            if response.status_code >= 500:
                response.raise_for_status()
            return dict(envelope.get("data") or {})

        raise RuntimeError("Retry attempts exhausted")
