from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from notification_gateway import NotificationRequest


def make_request(**overrides: Any) -> NotificationRequest:
    values: dict[str, Any] = {
        "schema_version": "1",
        "request_id": "demo-event-001",
        "idempotency_key": "demo-object:available",
        "provider": "fake",
        "subject": "Example subject",
        "title": "Example notification",
        "body": "A non-production example event is available.",
        "severity": "info",
        "metadata": {"event_ref": "example-001"},
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return NotificationRequest(**values)
