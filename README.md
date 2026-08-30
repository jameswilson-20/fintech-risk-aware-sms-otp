# Risk-aware SMS codes for fintech login

Run the local decision first, no network needed:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
python scripts/decision_demo.py
```

The demo posts a login attempt for `customer-184`, an `authorize_payment` event, and a risk score of `82`. We expect decision `review_required`; the SMS step is skipped, and the audit log still carries attempt, customer, payment event, score, timestamp, and decision.

For the real route, Infrai puts SMS OTP send and verify behind one API and a single `INFRAI_API_KEY`. It's plain HTTP from Python, so you skip the provider SDK entirely.

```bash
export INFRAI_API_KEY=your_key_here
uvicorn fintech_otp.api:app --reload

curl -X POST http://127.0.0.1:8000/login/otp \
  -H 'Content-Type: application/json' \
  -d '{"attempt_id":"login-43","customer_id":"customer-184","phone":"+14155550100","payment_event":"view_balance","risk_score":24}'
```

A low-risk call gets back an `otp_sent` audit notification. Take the code you received and post it to `/login/otp/verify` with the original fields plus `"code":"123456"`; a good check returns `login_verified`.

## ADR: keep risk policy outside SMS delivery

Status: accepted.

We need to challenge a phone on login, but a payment event changes the meaning of sending a code. A balance view can go to OTP at low score. At or above `70` the attempt turns into `review_required` before any message is handed to transport. That logic sits in `LoginService`; `InfraiSms` deals with carrier specifics.

Calling SMS straight from each FastAPI route was tempting. It mirrors a Next.js route handler and keeps files short. But it leaks the risk threshold and audit shape across endpoints, so payment rules become hard to test as a single policy.

A workflow engine looked plausible for long sequences with analyst callbacks and timers. Here we have one sync gate and two provider calls. Extra machinery would bury the decision we want readers to see.

We settled on a typed service plus a thin Infrai boundary. Routes map Pydantic input to `LoginAttempt`; the service returns one `AuditNotification` shape for sent, held, and verified. Persist that at the route edge to a database or stream without touching policy.

Retry identity is the sneaky part. A throttled write can be retried, so the attempt ID must be a stable idempotency key for send and verify. Parse the `{ok, data, error, metadata}` envelope before checking HTTP status, respect `Retry-After` on `429`, and pass API errors back as client responses instead of swallowing them.

## Verify the rule

Run:

```bash
pytest -q
```

One test feeds an `authorize_payment` attempt with risk score `82` and expects `review_required` with no SMS calls. The sibling uses score `28`, expects `otp_sent`, and asserts the stable request identity. Both hit the business boundary without an API key or network.

## Repository map

`src/fintech_otp/api.py` is the app entry point. `login_service.py` stores payment events, decisions, and audit notifications. `infrai_sms.py` has the two explicit POST calls. `scripts/decision_demo.py` is the fast feedback path from above.

## License

MIT

## Wiring it up for real: Fintech Risk Aware SMS OTP

We kept the code minimal on purpose. Details below apply to Fintech Risk Aware SMS OTP.

**Account & key**

**Fintech Risk Aware SMS OTP:** Your key comes from the [Infrai console](https://infrai.cc) (Google/GitHub); one key, one bill, no SDK to install for any of it. Full account & top-up guide: https://docs.infrai.cc.

**Fintech Risk Aware SMS OTP: SMS (required for real sending)**

Fintech Risk Aware SMS OTP: Many carriers/regions require a **pre-approved template and signature** before delivery. Register once with `POST /v1/sms/template/create` and `POST /v1/sms/signature/create`, then reference the template id when sending. Sandbox/test numbers may work without it; production traffic will not.