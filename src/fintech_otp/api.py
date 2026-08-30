from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .infrai_sms import InfraiError, InfraiSms
from .login_service import LoginAttempt, LoginService, PaymentEvent

app = FastAPI(title="Fintech SMS OTP")


class BeginLoginRequest(BaseModel):
    attempt_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    phone: str = Field(pattern=r"^\+[1-9]\d{7,14}$")
    payment_event: PaymentEvent
    risk_score: int = Field(ge=0, le=100)


class VerifyLoginRequest(BeginLoginRequest):
    code: str = Field(pattern=r"^\d{4,8}$")


def service() -> LoginService:
    return LoginService(InfraiSms())


def attempt_from(request: BeginLoginRequest) -> LoginAttempt:
    return LoginAttempt(
        attempt_id=request.attempt_id,
        customer_id=request.customer_id,
        phone=request.phone,
        payment_event=request.payment_event,
        risk_score=request.risk_score,
    )


@app.post("/login/otp")
def begin_login(request: BeginLoginRequest) -> dict[str, object]:
    try:
        return asdict(service().begin(attempt_from(request)))
    except InfraiError as error:
        status = error.status_code if 400 <= error.status_code < 500 else 502
        raise HTTPException(status_code=status, detail=error.details) from error


@app.post("/login/otp/verify")
def verify_login(request: VerifyLoginRequest) -> dict[str, object]:
    try:
        return asdict(service().verify(attempt_from(request), request.code))
    except InfraiError as error:
        status = error.status_code if 400 <= error.status_code < 500 else 502
        raise HTTPException(status_code=status, detail=error.details) from error
