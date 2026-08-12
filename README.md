# notification-gateway

`notification-gateway` is a small, typed Python service/library for durably accepting notification requests and delivering them through pluggable providers. v0.1 uses SQLite, an at-least-once worker, a minimal WSGI HTTP boundary, and a WeCom-compatible group-robot adapter.

It owns request validation, durable intake, provider routing, bounded retry, restart recovery, status, retention cleanup, and secret-safe delivery evidence. It does not own caller-specific monitoring, scheduling, scraping, product workflow, recipient management, or business rules.

## Status

v0.1 is alpha software for loopback or controlled private-network deployment. It is not a hardened public Internet edge and does not provide public-service authentication, tenant isolation, TLS termination, or general abuse protection.

## Install for development

The package is not yet represented here as an already-published PyPI release. Install from a checked-out source tree:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Python 3.11 or newer is required.

## Stable request contract

The Python model and `schemas/notification-request-v1.json` define the same strict v1 fields:

```json
{
  "schema_version": "1",
  "request_id": "demo-event-001",
  "idempotency_key": "demo-object:available",
  "provider": "wecom",
  "subject": "Example subject",
  "title": "Example notification",
  "body": "A non-production example event is available.",
  "severity": "info",
  "metadata": {"event_ref": "example-001"},
  "created_at": "2026-01-01T00:00:00.000000Z"
}
```

`request_id` identifies one durable notification occurrence. Replaying the exact same request is idempotent; reusing the same `request_id` with different content is a conflict. `idempotency_key` is caller correlation context and may be reused by later legitimate occurrences with new request IDs.

The model rejects missing/extra fields, non-UTC timestamps, invalid identifiers, unsupported severity, excessive nesting, non-JSON metadata, and oversized content.

## Python API

```python
import os
from datetime import UTC, datetime

from notification_gateway import (
    DeliveryWorker,
    NotificationGateway,
    NotificationRequest,
    SQLiteStore,
    WeComWebhookProvider,
)

store = SQLiteStore("runtime-data/notifications.sqlite3")
provider = WeComWebhookProvider(os.environ["WECOM_WEBHOOK_URL"])
gateway = NotificationGateway(store, [provider])

request = NotificationRequest(
    request_id="demo-event-001",
    idempotency_key="demo-object:available",
    provider="wecom",
    subject="Example subject",
    title="Example notification",
    body="A non-production example event is available.",
    severity="info",
    metadata={"event_ref": "example-001"},
    created_at=datetime.now(UTC),
)

accepted = gateway.accept(request)
DeliveryWorker(gateway).run_once()
print(gateway.status(accepted.status.request_id).state)
```

The intake transaction commits before provider I/O. The worker claims work using a short lease, releases the SQLite write transaction, calls the provider, and then persists the outcome. A crash after provider acceptance but before local acknowledgement can cause redelivery with the same `request_id`; exactly-once delivery is not claimed.

## Local HTTP boundary

Set runtime configuration without committing `.env`:

```bash
export WECOM_WEBHOOK_URL='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=runtime-secret'
notification-gateway --db runtime-data/notifications.sqlite3 serve
```

The server binds to `127.0.0.1:8787` by default and exposes:

- `POST /v1/notifications` — validate and durably enqueue;
- `GET /v1/notifications/{request_id}` — return status without message content;
- `GET /healthz` — local health.

Use `notification-gateway ... work-once` from a private supervisor or scheduler to deliver one due request. Use `notification-gateway ... purge` to delete terminal records after their configured retention periods.

Non-loopback binding is refused unless `--allow-non-loopback` is explicit, and it also requires a `NOTIFICATION_GATEWAY_AUTH_TOKEN` of at least 32 characters. That token is only a minimal service boundary. A reviewed deployment must additionally supply TLS termination, network access control, rate limiting, monitoring, and incident response.

`request_id` is an identifier, not an authorization credential.

## Retry and restart behavior

- Delivery is at-least-once.
- Retry uses bounded exponential backoff with explicit maximum attempts and delay.
- Retry state and attempt evidence survive restart.
- Expired in-flight leases become retryable work using the same request ID.
- Permanent failures and exhausted retries become `dead`.
- Provider I/O does not occur inside a SQLite write transaction.
- Provider errors are normalized and bounded before persistence.

## Secrets and privacy

Webhook URLs and authentication tokens come only from runtime configuration. Secret fields are excluded from object representations. Full URLs, query strings, headers, raw provider responses, and unsanitized provider exceptions must not appear in logs, HTTP errors, SQLite, attempts, fixtures, or audit evidence.

Notification content and metadata may contain personal information if a caller supplies it. The gateway does not decide whether that processing is lawful. Callers/operators remain responsible for purpose limitation, data minimization, lawful basis, notices/consents, sensitive and minor data, retention, deletion, individual rights, provider contracts, and cross-border transfer rules.

Prefer generic content plus an opaque event reference. Never put passwords, tokens, recovery codes, identity-document numbers, financial credentials, unrestricted student profiles, or other unnecessary personal information in notification requests.

See [privacy and mainland-China deployment boundary](docs/privacy-and-mainland-china.md) and [security policy](SECURITY.md).

## Retention

SQLite durability is not a permanent message archive. `purge_terminal` removes delivered/dead requests and their attempts after explicit retention periods while leaving pending/retryable authority intact. Operators must separately manage SQLite WAL files, backups, filesystem permissions, encrypted storage where appropriate, and incident-driven deletion requirements.

## Add a provider

A provider implements a stable `name` and `deliver(NotificationRequest) -> DeliveryResult`. Provider transport must be injectable so tests never use external services. A provider must document its operator, expected data region when known, possible cross-border transfer, accepted data classification, size limits, retry semantics, and approval requirements.

No provider should claim that data remains in a jurisdiction based only on a hostname.

## Verification

```bash
pytest
ruff format --check .
ruff check .
mypy
python -m pip check
python -m build
git diff --check
```

GitHub Actions runs the full suite on Python 3.11–3.13. Tests must not use external network access.

## License and trademarks

Licensed under Apache-2.0.

WeCom, WeChat, and related names are trademarks of their respective owners. This independent project is not affiliated with, endorsed by, or sponsored by Tencent. Provider names are used only to describe interoperability with user-configured endpoints.
