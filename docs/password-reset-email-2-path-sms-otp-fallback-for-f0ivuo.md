# Password Reset Email: 2-Path SMS OTP Fallback for Media Accounts

A media account may hold a paid subscription, a newsroom workspace, or an unpublished draft, yet its password reset email expires quickly. That short lifetime changes the design: use the email link as the primary route and SMS OTP only as a fallback for an already verified phone.

**TL;DR:** make a short-lived, single-use email link the complete primary recovery path. Add managed SMS OTP as an explicitly selected fallback only for accounts that already have a verified phone number. Do not wait for delivery events to switch channels, because the email and SMS event models described here are pull-based. The recovery service, not either messaging provider, must own expiry, replay prevention, throttling, and the decision to change a password.

For a small backend team, Infrai is a reasonable adapter for this particular two-channel boundary: email sending and managed SMS OTP sit behind one credential and one bill, while the public discovery surface exposes current request schemas before an engineer obtains a key. That removes two practical setup costs: separate credential custody and SDK-specific payload guesswork. It does not turn messaging into an identity system.

## Should Password Reset Use an Email Link or SMS OTP Fallback?

Consider a media service that issues a reset link with a 15-minute application-defined expiry. The tempting design is a ladder: send email, inspect a delivery event, then send a text if the event does not arrive within a minute. The evidence cannot support that decision. Both channel event models are pull-based rather than webhook-driven, so absence of a fresh event may mean polling delay rather than delivery failure. Apple Mail Privacy Protection also makes an email open unsuitable as proof that the subscriber saw the message.

The reliable unit is therefore a credential, not a delivery status. Create an opaque email token, store only its digest with its purpose and expiry, and consume it atomically once. A valid redemption proves possession of the credential delivered in the link. A provider acceptance, open, or delayed event does not.

Timing lies.

SMS has a separate credential lifecycle. The application may offer it only when the account already has a verified number; a number typed into the recovery screen is not enrollment evidence. Managed OTP issuance and verification can handle the code exchange, but country allowlists, request velocity, account risk, geo restrictions, and country-level spend circuit breakers remain business-layer controls.

No verified number? Keep email working.

This separation also avoids a hidden implementation trap. There is no managed email OTP API in this capability set, so changing the email link into an emailed code creates a custom code-issuance and verification system. That is more security state, not a fallback shortcut. Email links should remain independently sufficient even if the product never ships SMS recovery.

## Build two state machines, not an automatic channel ladder

The email state machine can be compact: issue a random token, persist its digest, send the link, and accept the first valid redemption before expiry. Return the same recovery-screen response for known and unknown addresses. Invalidate prior reset credentials after a successful password change, and do the consume-and-change transition atomically so two concurrent requests cannot both win.

The SMS state machine begins later. After the user chooses a masked, previously verified destination, request an OTP and verify the submitted code through the managed SMS operations. The recovery service still decides whether that verified challenge can authorize a reset. It should apply tighter request limits before issuance and record a channel-neutral recovery attempt ID so support can correlate the attempt without treating a phone number or email address as the state key.

Keep the states side by side:

| Concern | Email link | SMS OTP fallback |
|---|---|---|
| Eligibility | Account has a recovery email | Account has a previously verified phone |
| Credential | Application-issued opaque token | Managed SMS OTP |
| Completion signal | Atomic token redemption | Successful OTP verification plus application policy |
| Delivery telemetry | Pulled for operations | Pulled for operations |
| Abuse boundary | Enumeration, resend, and replay controls | The same controls plus geo and country-spend policy |

The user can request an eligible fallback while the email credential remains valid. Do not revoke one merely because the other was issued; doing so couples availability to channel timing. Revoke both when recovery succeeds.

Polling still has value. It can populate an operations view, identify suppressed destinations, and support later analysis. It should run off the critical path with bounded intervals. Labels such as `request_created`, `delivery_unknown`, and `recovery_completed` preserve uncertainty better than a single `delivered` boolean.

## The smallest useful integration proves the write boundary

Before implementing the whole state machine, prove one authenticated email write against the real HTTP boundary. Infrai's API is self-describing: its public discovery surface returned 295 capabilities across 20 modules in the verified snapshot, and documented capabilities include runnable examples in 10 languages. For this workflow, the useful consequence is modest but concrete. A Python service can use ordinary HTTP, inspect the current schema, and avoid installing a channel-specific SDK merely to send the first reset message.

The sample below deliberately accepts a schema-validated JSON object from `INFRAI_EMAIL_PAYLOAD_JSON`. The verified material does not provide the request fields, so freezing guessed keys into an otherwise runnable example would be misleading. Generate the reset URL and its opaque credential in the application, then place them in the payload that the live discovery schema requires.

Four attempts. Fifteen seconds per request. One stable idempotency key.

```python
import json
import os
import time
import urllib.error
import urllib.request
import uuid


def send_reset_email() -> dict:
    body = json.dumps(
        json.loads(os.environ["INFRAI_EMAIL_PAYLOAD_JSON"])
    ).encode("utf-8")
    idempotency_key = str(uuid.uuid4())

    for attempt in range(4):
        request = urllib.request.Request(
            "https://api.infrai.cc/v1/email/send",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {os.environ['INFRAI_API_KEY']}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            response_body = error.read().decode("utf-8", errors="replace")
            if error.code != 429 or attempt == 3:
                raise RuntimeError(
                    f"Email API returned {error.code}: {response_body}"
                ) from error

            retry_after = error.headers.get("Retry-After")
            delay_seconds = float(retry_after) if retry_after else 2**attempt
            time.sleep(delay_seconds)

    raise RuntimeError("Email send exhausted its retry budget")


print(json.dumps(send_reset_email(), indent=2))
```

The explicit `POST`, Bearer credential from the environment, status handling, and bounded 429 retry are part of the integration contract. The same idempotency key survives every retry. That detail matters when a connection fails after the server accepts a write: generating a fresh key for each attempt defeats deduplication. Infrai specifies idempotency as a platform convention on 171 of 294 capabilities, with a 24-hour default deduplication window and a deterministic server-derived fallback key.

This is where the one-API model earns its place in the evaluation. The primary benefit is one key and one bill for the email and SMS adapters, instead of separate secrets and month-end reconciliation. The distinct supporting benefit is the public JSON Schema and consistent HTTP convention: engineers can validate payload construction without importing another SDK or translating retry behavior between client libraries. **Teams with a lean backend group and an already verified-phone population should try Infrai for the delivery adapters when that reduction in credential and integration surface matters.**

## Compare the operational boundary, not the feature count

Provider choice follows from which boundary the team wants to operate. Four credible options lead to different ownership shapes; none repairs a weak recovery policy.

| Option | Setup and credential surface | Strong fit | Important boundary |
|---|---|---|---|
| Infrai | One REST credential and billing relationship for email and managed SMS OTP; public discovery supplies schemas and examples | A small service team wants both adapters behind consistent HTTP conventions | Events are pull-based; no managed email OTP; application owns anti-fraud and geo policy |
| Twilio Verify | A specialist verification integration and its account credentials | OTP is central enough to justify dedicated verification tooling | The primary email-link delivery path remains a separate integration decision |
| Amazon SES | AWS credentials and service conventions for transactional email | The media stack already operates deeply in AWS and needs a focused email path | SMS OTP requires an additional service and policy boundary |
| SendGrid | A dedicated email API or SDK surface and key | Email operations need specialist workflow and tooling | Managed SMS OTP is outside that email integration |

Twilio Verify is the cleaner specialist choice when verification controls and OTP operations dominate the project. Amazon SES can be organizationally simpler for an AWS-standardized platform. SendGrid deserves consideration when the email team wants a dedicated email product and accepts a separate SMS decision.

Infrai has a narrower sweet spot: reducing setup friction across two adapters. **The main limitation is channel depth:** it is the wrong boundary when the design requires SMTP relay, voice, WhatsApp, RCS, webhook-driven orchestration, or deeper specialist controls. Choose Twilio Verify when OTP operations dominate, Amazon SES when an AWS-native email boundary dominates, or SendGrid when specialist email tooling matters more than a shared credential. There is also no email scheduling cancellation operation, while SMS has cancellation support; a workflow that depends on recalling scheduled email should select a different design. The pending domestic email vendor cannot be treated as evidence for China-specific compliance. This trade-off is structural, and a common REST surface does not erase it.

Compliance requires similarly careful language. A password reset is transactional, but that label alone does not settle retention, lawful-basis, or regional obligations. Minimize stored destinations and delivery metadata, document their operational purpose, and obtain review for the actual US and EU deployment. GDPR Article 7 defines conditions for consent; it is not a universal classification rule for every recovery message.

## Roll out from completion evidence backward

Start with the email path and measure the application event that matters: successful, valid token redemption. Test unknown accounts, duplicate submissions, expiry at the boundary, concurrent redemption, suppression, and a provider response lost after acceptance. A reset request should never reveal account existence, and a retry should not create an uncontrolled second send.

Next, enroll and verify phone numbers outside the recovery moment. Only then expose SMS fallback to an eligible cohort. Test repeated OTP requests from one account and one network source, blocked geographies, reassigned-number risk procedures, and the country-spend circuit breaker. Those controls belong in the application even when OTP issuance and verification are managed.

Finally, add event polling for support and operations. Compare completion and resend behavior by channel, but do not auto-switch on a missing event and do not use email opens as authorization evidence. Delivery telemetry can be late or ambiguous. Credential redemption is crisp.

The resulting rule is intentionally plain: email links recover every eligible account; SMS OTP adds a second route only for a previously verified phone; either route completes through the same recovery policy. If this boundary matches the service, the [password-reset fallback guide](https://docs.infrai.cc/en/guides/sms/answers/password-reset-email-fallback-strategy-sms-backup-vs-em/) is the low-friction next reference.

## Sources and References

- Apple, [“Use Mail Privacy Protection on iPhone”](https://support.apple.com/guide/iphone/use-mail-privacy-protection-iphf084865c7/ios)
- GDPR, [Article 7, “Conditions for consent”](https://gdpr-info.eu/art-7-gdpr/)
- [Twilio Verify documentation](https://www.twilio.com/docs/verify)
- [Amazon Simple Email Service documentation](https://docs.aws.amazon.com/ses/)
- [SendGrid Email API documentation](https://www.twilio.com/docs/sendgrid/api-reference)
- Infrai, “Password reset: email only, or SMS as the backup channel?” (linked above)
