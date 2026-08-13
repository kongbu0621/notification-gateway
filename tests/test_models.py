from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import make_request
from jsonschema import Draft202012Validator, FormatChecker

from notification_gateway import DeliveryResult, NotificationRequest, ValidationError
from notification_gateway.models import MAX_BODY_CHARS, MAX_METADATA_DEPTH


def test_deterministic_round_trip_and_schema() -> None:
    request = make_request(metadata={"z": 1, "a": [True, None, "值"]})
    encoded = request.to_json()
    assert encoded == request.to_json()
    assert NotificationRequest.from_json(encoded) == request
    assert encoded.index('"a"') < encoded.index('"z"')

    schema = json.loads(Path("schemas/notification-request-v1.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(request.to_dict())) == []


def test_repr_hides_request_content_and_delivery_evidence() -> None:
    request = make_request(
        idempotency_key="private-correlation",
        subject="private subject",
        title="private title",
        body="private body",
        metadata={"private": "value"},
    )
    rendered = repr(request)
    for private_value in (
        "private-correlation",
        "private subject",
        "private title",
        "private body",
        "value",
    ):
        assert private_value not in rendered

    result = DeliveryResult("fake", "private-message-id", {"evidence": "private-evidence"})
    assert "private-message-id" not in repr(result)
    assert "private-evidence" not in repr(result)


def test_schema_rejects_missing_extra_and_invalid_fields() -> None:
    schema = json.loads(Path("schemas/notification-request-v1.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    value = make_request().to_dict()
    del value["title"]
    assert list(validator.iter_errors(value))
    value = make_request().to_dict() | {"extra": True}
    assert list(validator.iter_errors(value))
    value = make_request().to_dict() | {"created_at": "2026-01-01T08:00:00+08:00"}
    assert list(validator.iter_errors(value))


@pytest.mark.parametrize(
    "change, message",
    [
        ({"schema_version": "2"}, "schema_version"),
        ({"request_id": "bad id"}, "request_id"),
        ({"idempotency_key": ""}, "idempotency_key"),
        ({"provider": "Bad"}, "provider"),
        ({"subject": "x" * 129}, "subject"),
        ({"title": " "}, "title"),
        ({"body": "x" * (MAX_BODY_CHARS + 1)}, "body"),
        ({"severity": "urgent"}, "severity"),
        ({"created_at": datetime(2026, 1, 1)}, "created_at"),
    ],
)
def test_model_rejects_invalid_values(change: dict[str, object], message: str) -> None:
    with pytest.raises((ValidationError, TypeError), match=message):
        make_request(**change)


def test_from_dict_is_strict_and_requires_json_types() -> None:
    value = make_request().to_dict()
    del value["title"]
    with pytest.raises(ValidationError, match="missing"):
        NotificationRequest.from_dict(value)
    value = make_request().to_dict() | {"extra": True}
    with pytest.raises(ValidationError, match="unknown"):
        NotificationRequest.from_dict(value)
    value = make_request().to_dict()
    value["metadata"] = {"bad": object()}
    with pytest.raises(ValidationError, match="JSON-compatible"):
        NotificationRequest.from_dict(value)
    with pytest.raises(ValidationError, match="JSON object"):
        NotificationRequest.from_dict([])
    with pytest.raises(ValidationError, match="valid JSON"):
        NotificationRequest.from_json("{")


def test_created_at_requires_z_but_constructor_normalizes_utc() -> None:
    value = make_request().to_dict()
    value["created_at"] = "2026-01-01T08:00:00+08:00"
    with pytest.raises(ValidationError, match="end in Z"):
        NotificationRequest.from_dict(value)

    plus_eight = timezone(timedelta(hours=8))
    request = make_request(created_at=datetime(2026, 1, 1, 8, tzinfo=plus_eight))
    assert request.to_dict()["created_at"] == "2026-01-01T00:00:00.000000Z"
    assert request.created_at.tzinfo == UTC


def test_metadata_is_copied_bounded_and_finite() -> None:
    metadata = {"nested": {"value": 1}}
    request = make_request(metadata=metadata)
    metadata["later"] = True
    metadata["nested"]["value"] = 2
    assert request.to_dict()["metadata"] == {"nested": {"value": 1}}

    nested: object = "leaf"
    for _ in range(MAX_METADATA_DEPTH + 1):
        nested = {"next": nested}
    with pytest.raises(ValidationError, match="nesting"):
        make_request(metadata=nested)
    with pytest.raises(ValidationError, match="finite"):
        make_request(metadata={"bad": float("nan")})
    with pytest.raises(ValidationError, match="too many"):
        make_request(metadata={str(index): index for index in range(33)})
    with pytest.raises(ValidationError, match="too large"):
        make_request(metadata={"large": "x" * 4096})


def test_from_dict_rejects_wrong_primitive_types() -> None:
    for field in ("request_id", "idempotency_key", "provider", "subject", "title", "body"):
        value = make_request().to_dict()
        value[field] = 1
        with pytest.raises(ValidationError, match=field):
            NotificationRequest.from_dict(value)
    value = make_request().to_dict()
    value["created_at"] = "not-a-dateZ"
    with pytest.raises(ValidationError, match="RFC 3339"):
        NotificationRequest.from_dict(value)
