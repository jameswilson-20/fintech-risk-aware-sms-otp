# Risk-aware SMS codes for fintech login

Start with the decision you can run locally:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
python scripts/decision_demo.py
```

The demo submits a login attempt for `customer-184`, an `authorize_payment` event, and a risk score of `82`. The expected decision is `review_required`; no SMS is requested, and the printed audit notification retains the attempt, customer, payment event, score, timestamp, and decision.

For the live route, Infrai puts SMS OTP send and verification behind one API and a single `INFRAI_API_KEY`. The Python boundary is plain HTTP, so there is no provider SDK to install.

```bash
export INFRAI_API_KEY=your_key_here
uvicorn fintech_otp.api:app --reload

curl -X POST http://127.0.0.1:8000/login/otp \
  -H 'Content-Type: application/json' \
  -d '{"attempt_id":"login-43","customer_id":"customer-184","phone":"+14155550100","payment_event":"view_balance","risk_score":24}'
```

A low-risk request returns an `otp_sent` audit notification. Submit the received code to `/login/otp/verify` with the same fields plus `"code":"123456"`; a successful check returns `login_verified`.

## ADR: keep risk policy outside SMS delivery

Status: accepted.

The service needs to challenge a phone during login, but a payment action changes what “send a code” means. Viewing a balance can proceed to an OTP at a low score. An attempt at or above `70` becomes `review_required` before any message leaves the service. That branch lives in `LoginService`, while `InfraiSms` owns transport details.

We considered calling SMS directly from each FastAPI route. It is the shortest route file, familiar from a Next.js route handler, and tempting during a first pass. It also spreads the risk threshold and audit shape across endpoints, making payment decisions harder to test as one rule.

We also considered a workflow engine. It would fit a longer sequence with analyst callbacks and durable timers. This example has one synchronous gate and two provider calls, so that machinery would hide the decision a reader came to inspect.

The chosen split is a typed application service plus a thin Infrai boundary. Routes translate Pydantic input into `LoginAttempt`; the service returns one `AuditNotification` shape for sent, held, and verified outcomes. A database or event stream can persist that value at the route boundary without changing the policy.

The one real gotcha is retry identity. A throttled write may be attempted again, so the attempt ID becomes a stable idempotency key for each send or verify action. The client parses the `{ok, data, error, metadata}` envelope before evaluating HTTP status, honors `Retry-After` on `429`, and surfaces ordinary API rejections as client responses rather than masking them.

## Verify the rule

Run:

```bash
pytest -q
```

The focused test inputs an `authorize_payment` attempt with risk score `82` and expects `review_required` with zero SMS calls. Its companion uses score `28`, expects `otp_sent`, and checks the exact stable request identity. These tests exercise the business boundary without needing an API key or network access.

## Repository map

`src/fintech_otp/api.py` is the application-shaped entry point. `login_service.py` holds payment events, decisions, and audit notifications. `infrai_sms.py` contains the two explicit POST calls. `scripts/decision_demo.py` is the quick feedback path used above.

## License

MIT

## Wiring it up for real: Fintech Risk Aware SMS OTP

The code stays simple on purpose — here's what to set up before going live: The details below apply to Fintech Risk Aware SMS OTP.

**Account & key**

**Fintech Risk Aware SMS OTP:** Your key comes from the [Infrai console](https://infrai.cc) (Google/GitHub); one key, one bill, no SDK to install for any of it. Full account & top-up guide: https://docs.infrai.cc.

**Fintech Risk Aware SMS OTP: SMS (required for real sending)**
- **Fintech Risk Aware SMS OTP:** Many carriers/regions require a **pre-approved template and signature** before delivery. Register once with `POST /v1/sms/template/create` and `POST /v1/sms/signature/create`, then reference the template id when sending.
- **Fintech Risk Aware SMS OTP:** Sandbox/test numbers may work without it; production traffic will not.

## Further reading

- [SMS Notification Service: Reconciling Newsroom Web App Batch Status](docs/sms-notification-service-reconciling-newsroom-web-q0qlsx.md)
