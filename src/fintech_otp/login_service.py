from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4


class PaymentEvent(StrEnum):
    VIEW_BALANCE = "view_balance"
    ADD_BENEFICIARY = "add_beneficiary"
    AUTHORIZE_PAYMENT = "authorize_payment"


class LoginDecision(StrEnum):
    OTP_SENT = "otp_sent"
    REVIEW_REQUIRED = "review_required"
    LOGIN_VERIFIED = "login_verified"


class SmsGateway(Protocol):
    def request_otp(self, *, to: str, idempotency_key: str) -> dict[str, Any]:
        raise AssertionError

    def verify_otp(
        self, *, to: str, code: str, idempotency_key: str
    ) -> dict[str, Any]:
        raise AssertionError


@dataclass(frozen=True)
class LoginAttempt:
    attempt_id: str
    customer_id: str
    phone: str
    payment_event: PaymentEvent
    risk_score: int


@dataclass(frozen=True)
class AuditNotification:
    event_id: str
    attempt_id: str
    customer_id: str
    decision: LoginDecision
    payment_event: PaymentEvent
    risk_score: int
    occurred_at: str
    provider_reference: str | None


class LoginService:
    REVIEW_THRESHOLD = 70

    def __init__(self, sms: SmsGateway) -> None:
        self.sms = sms

    def begin(self, attempt: LoginAttempt) -> AuditNotification:
        if attempt.risk_score >= self.REVIEW_THRESHOLD:
            return self._audit(attempt, LoginDecision.REVIEW_REQUIRED, None)

        result = self.sms.request_otp(
            to=attempt.phone,
            idempotency_key=f"login:{attempt.attempt_id}:send",
        )
        reference = str(result.get("message_id") or result.get("id") or "submitted")
        return self._audit(attempt, LoginDecision.OTP_SENT, reference)

    def verify(self, attempt: LoginAttempt, code: str) -> AuditNotification:
        result = self.sms.verify_otp(
            to=attempt.phone,
            code=code,
            idempotency_key=f"login:{attempt.attempt_id}:verify",
        )
        reference = str(result.get("message_id") or result.get("id") or "verified")
        return self._audit(attempt, LoginDecision.LOGIN_VERIFIED, reference)

    @staticmethod
    def _audit(
        attempt: LoginAttempt,
        decision: LoginDecision,
        provider_reference: str | None,
    ) -> AuditNotification:
        return AuditNotification(
            event_id=str(uuid4()),
            attempt_id=attempt.attempt_id,
            customer_id=attempt.customer_id,
            decision=decision,
            payment_event=attempt.payment_event,
            risk_score=attempt.risk_score,
            occurred_at=datetime.now(UTC).isoformat(),
            provider_reference=provider_reference,
        )
