from dataclasses import asdict
from typing import Any

from fintech_otp.login_service import LoginAttempt, LoginService, PaymentEvent


class DemoSms:
    def request_otp(self, *, to: str, idempotency_key: str) -> dict[str, Any]:
        return {"message_id": f"demo-{idempotency_key}"}

    def verify_otp(
        self, *, to: str, code: str, idempotency_key: str
    ) -> dict[str, Any]:
        return {"message_id": f"demo-{idempotency_key}"}


attempt = LoginAttempt(
    attempt_id="login-2026-0042",
    customer_id="customer-184",
    phone="+14155550100",
    payment_event=PaymentEvent.AUTHORIZE_PAYMENT,
    risk_score=82,
)

print(asdict(LoginService(DemoSms()).begin(attempt)))
