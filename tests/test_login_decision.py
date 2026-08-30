from typing import Any

from fintech_otp.login_service import (
    LoginAttempt,
    LoginDecision,
    LoginService,
    PaymentEvent,
)


class RecordingSms:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def request_otp(self, *, to: str, idempotency_key: str) -> dict[str, Any]:
        self.sent.append((to, idempotency_key))
        return {"message_id": "sms-42"}

    def verify_otp(
        self, *, to: str, code: str, idempotency_key: str
    ) -> dict[str, Any]:
        return {"message_id": "sms-42"}


def attempt(risk_score: int) -> LoginAttempt:
    return LoginAttempt(
        attempt_id="attempt-42",
        customer_id="customer-7",
        phone="+14155550100",
        payment_event=PaymentEvent.AUTHORIZE_PAYMENT,
        risk_score=risk_score,
    )


def test_high_risk_payment_requires_review_without_sending_otp() -> None:
    sms = RecordingSms()

    notification = LoginService(sms).begin(attempt(risk_score=82))

    assert notification.decision is LoginDecision.REVIEW_REQUIRED
    assert notification.payment_event is PaymentEvent.AUTHORIZE_PAYMENT
    assert notification.risk_score == 82
    assert sms.sent == []


def test_low_risk_payment_sends_one_idempotent_otp_request() -> None:
    sms = RecordingSms()

    notification = LoginService(sms).begin(attempt(risk_score=28))

    assert notification.decision is LoginDecision.OTP_SENT
    assert notification.provider_reference == "sms-42"
    assert sms.sent == [("+14155550100", "login:attempt-42:send")]
