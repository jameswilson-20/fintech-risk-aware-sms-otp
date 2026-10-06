# SMS Notification Service: Reconciling Newsroom Web App Batch Status

TL;DR: Choose the SMS service whose status API lets your application reconcile every submitted recipient into a terminal or explicitly unresolved state, then make suppression a local invariant before each send. For a media company pushing US and EU breaking-news batches without webhooks, the smallest dependable integration is not `send()` plus a timer. It is an outbox, a recipient-level poller, and a suppression ledger that can explain why a number was skipped.

The decision rule is concrete: reject any integration that cannot expose a stable message identifier, recipient-level status, and enough failure classification to distinguish a retryable delivery gap from an invalid destination. A tidy SDK matters less than those three pieces. This framing also keeps the design vendor-neutral; the application owns truth about audience eligibility while the transport reports what happened after acceptance.

This is an architecture decision record for a newsroom alert system, where one editorial event can fan out across regions and a stale audience list can repeatedly target the same bad number. The primary optimization is integration effort, but effort must include the work needed to investigate a missing alert at 2 a.m., not merely the first successful request.

## How should a web app reconcile SMS batch status and suppressions?

The central invariant is simple: **a suppressed recipient never enters a new transport batch**. Enforce it at dispatch time, not only when an editor creates a campaign. A campaign may wait in a queue while a previous poll discovers that one of its recipients is invalid. Eligibility checked hours earlier is stale by definition.

The second invariant links each transport attempt to one immutable application record. Store the campaign ID, normalized recipient key, region, transport message ID, attempt number, submission time, and last observed status. Do not overwrite an old attempt when retrying. Without that history, an operator cannot tell whether a delivered status belongs to the first attempt or the retry.

The third invariant makes suppression monotonic for permanent failures. Once the application records an invalid-recipient outcome, routine delivery processing cannot quietly reactivate that destination. Reactivation needs a separate, auditable path based on a newly supplied or newly verified number. That boundary matters because delivery status and audience consent answer different questions: one describes transport outcome; the other determines whether sending is allowed.

No silent reactivation.

These rules create four failure boundaries:

1. Before submission, the application owns consent, audience selection, deduplication, region policy, and suppression.
2. At submission, it records either a transport identifier or an explicit submission failure; an ambiguous timeout is not proof that nothing was accepted.
3. During polling, it treats nonterminal and unknown results as unresolved, never as delivered and never as permission to duplicate the message.
4. After a permanent recipient failure, it writes suppression and the evidence for that decision in the same application transaction.

Short outages become boring under this model. A poll can be late. A batch can remain unresolved. Neither event changes recipient eligibility or invents a second attempt.

Unknown stays unknown.

## Compare integration shapes by reconciliation work

The apparent simplicity of an option is misleading if it pushes state recovery into manual operations. For this scenario, compare the amount of application-owned reconciliation rather than counting SDK calls.

| Integration shape | Application state required | Failure visibility | Suppression behavior | Best fit |
|---|---|---|---|---|
| Submit and forget | Campaign and request result | Submission only | Pre-send list checks only | Disposable, noncritical notices where delivery evidence is unnecessary |
| Submit, then poll aggregate batches | Batch ID and aggregate counters | Detects incomplete batches but may not identify the recipient | Requires a separate recipient evidence source | Systems where the transport contract guarantees recipient detail elsewhere |
| Submit, then poll each recipient result | Outbox attempt, transport ID, status cursor, suppression evidence | Maps outcomes to a specific attempt | Permanent recipient outcomes can update the local ledger | Newsroom alerts that need explainable skips without webhooks |
| Import transport suppressions periodically | Local ledger plus import cursor and provenance | Depends on export freshness and identity matching | Useful as defense in depth, risky as the only gate | Migration, recovery, or reconciliation with an existing transport account |

The third shape has more moving parts than submit-and-forget, yet it minimizes hidden integration work. Support staff can trace a complaint from campaign to attempt to observed outcome. Engineers can replay a page of polling without resending content. Compliance reviewers can inspect the reason and time for a suppression decision.

Polling also creates a capacity tradeoff. A fixed one-second loop is easy to write and wasteful at rest; a very slow loop leaves invalid recipients eligible for longer. Use a bounded schedule instead: poll soon after submission, back off while a result remains nonterminal, and move records that exceed an operational deadline into an unresolved queue. The deadline is a local policy, not a claim that the transport failed.

For example, a team might choose delays of 15 seconds, 60 seconds, 5 minutes, and then 15 minutes, with no more than 200 records claimed by one worker. Those are starting parameters, not universal delivery promises. Record queue age and status age so production data can justify changing them.

## Put the critical path in one transaction boundary

The transport client below is deliberately generic. Its contract returns recipient-level observations; the application converts only an explicit permanent invalid-recipient result into suppression. Other terminal failures remain visible for policy review instead of being folded into one dangerous `failed` bucket.

```python
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol


class Outcome(Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    TEMPORARY_FAILURE = "temporary_failure"
    INVALID_RECIPIENT = "invalid_recipient"
    OTHER_TERMINAL_FAILURE = "other_terminal_failure"


@dataclass(frozen=True)
class Observation:
    transport_id: str
    outcome: Outcome
    observed_at: datetime
    evidence_code: str | None


class Transport(Protocol):
    def status(self, transport_id: str) -> Observation: ...


def reconcile_due_attempts(db, transport: Transport, now: datetime) -> int:
    attempts = db.claim_due_attempts(limit=200, claimed_at=now)
    reconciled = 0

    for attempt in attempts:
        try:
            observation = transport.status(attempt.transport_id)
        except TimeoutError:
            db.defer_poll(
                attempt_id=attempt.id,
                next_poll_at=now + timedelta(minutes=5),
                reason="status_timeout",
            )
            continue

        with db.transaction():
            current = db.lock_attempt(attempt.id)
            if current.is_terminal:
                continue

            db.append_observation(
                attempt_id=current.id,
                outcome=observation.outcome.value,
                evidence_code=observation.evidence_code,
                observed_at=observation.observed_at,
            )

            if observation.outcome is Outcome.INVALID_RECIPIENT:
                db.suppress_recipient(
                    recipient_key=current.recipient_key,
                    reason="invalid_recipient",
                    source_attempt_id=current.id,
                    recorded_at=now,
                )

            db.advance_attempt(current.id, observation.outcome.value, now)
            reconciled += 1

    return reconciled


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
```

Idempotency belongs on both sides of the network boundary. Before submission, allocate an application attempt ID and persist a request fingerprint. If the submission times out ambiguously, query by a supported idempotency key or place the attempt in a review/recovery state; do not immediately send the same newsroom alert again. During reconciliation, make repeated observations harmless through a unique constraint such as `(attempt_id, outcome, observed_at)` or, when timestamps are not stable, a transport event/version identifier supplied by the contract.

There is a less obvious race at dispatch. A worker can read “eligible,” pause, and submit after another worker records suppression. Close that window by claiming recipients and rechecking suppression within the same local transaction that creates the outbox row. The network call should happen after commit. A separate sender drains the outbox and records the transport identifier, which avoids holding a database transaction open across a remote call.

Evidence beats inference.

Normalization needs restraint. Use the same canonical recipient key for audience membership, attempts, and suppression, but retain the original input separately for audit. Do not infer that two distinct identities are the same merely because a formatting library produces a similar string. Regional validation and consent rules belong in explicit policy modules, with their configuration version attached to the dispatch decision.

## Operate the gap between accepted and known

An accepted submission is not a delivery result. Model the gap rather than hiding it. Useful operational views include the count and oldest age of unresolved attempts, polling error rate, transitions into permanent suppression, attempts blocked by suppression, and the number of ambiguous submissions awaiting recovery. Break those views down by region and campaign type, but protect recipient data in logs and dashboards.

Alert on age, not just volume. Ten unresolved messages for 40 minutes may be more actionable than 10,000 that were submitted 20 seconds ago. Likewise, a sudden rise in invalid-recipient outcomes can point to an audience-import problem, while a broad rise in temporary outcomes calls for a different response. The classifier should preserve that distinction all the way from the transport response to the operator view.

Testing should target state transitions and races. A compact suite needs cases for repeated identical observations, terminal status followed by stale nonterminal status, suppression arriving between campaign creation and dispatch, an ambiguous submit timeout, two pollers claiming the same attempt, and a recipient correction that creates a new identity without deleting old evidence. Use a fake transport with scripted observations; live tests can confirm contract mapping, but they should not be the only proof that the state machine works.

Deployment can be incremental. Start by writing attempt records alongside the existing sender, then run the poller in observation-only mode. Compare its terminal counts with existing operational evidence. Enable suppression writes only after the mapping from external outcomes to the narrow internal taxonomy is reviewed. Finally, make the local ledger a dispatch gate. Each phase has a rollback that does not discard history.

The compliance boundary deserves equal care. SMS eligibility must come from the policy applicable to the audience and message; an email compliance guide does not define that SMS policy. The FTC's CAN-SPAM guide is still useful here as a reminder that channel-specific obligations exist, while the architecture keeps consent evidence distinct from transport health. Legal review must supply the actual US and EU SMS rules and retention policy before launch.

## Why reject transport-owned suppression as the sole control?

The rejected option is to let the transport maintain the only suppression list and consult it implicitly during submission. It removes a table and a reconciliation step from the application, so it is valid for a single-purpose system with one transport account, no independent audit requirement, and no need to explain eligibility before a request leaves the application.

It is a poor primary control for this newsroom case. The application cannot reliably answer why a recipient was excluded before submission, tie that exclusion to its own attempt history, or preserve the decision when the transport boundary changes. Periodic imports also create a freshness interval during which the local audience still appears eligible.

Keep transport-side suppression enabled when available; it is a useful second barrier. The ADR rejects only sole ownership. **The application ledger decides who may enter a batch, while recipient-level polling supplies evidence that can tighten that ledger.** That division adds a small, explicit state machine and removes a much larger class of untraceable behavior.

## References

- https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business
- https://resend.com/docs/introduction
