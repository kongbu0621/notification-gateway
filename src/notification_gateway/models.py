"""Stable, provider-neutral v1 request and result models."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Final, Literal, cast

from .exceptions import ValidationError

SCHEMA_VERSION: Final = "1"
MAX_SUBJECT_CHARS: Final = 128
MAX_TITLE_CHARS: Final = 256
MAX_BODY_CHARS: Final = 16_384
MAX_METADATA_KEYS: Final = 32
MAX_METADATA_DEPTH: Final = 4
MAX_METADATA_BYTES: Final = 4_096
MAX_REQUEST_BYTES: Final = 32_768
MAX_IDEMPOTENCY_KEY_CHARS: Final = 256

Severity = Literal["info", "warning", "error", "critical"]
SEVERITIES: Final = frozenset({"info", "warning", "error", "critical"})
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PROVIDER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_IDEMPOTENCY_KEY = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_EXPECTED_FIELDS: Final = frozenset(
    {
        "schema_version",
        "request_id",
        "idempotency_key",
        "provider",
        "subject",
        "title",
        "body",
        "severity",
        "metadata",
        "created_at",
    }
)


def _reject_json_constant(constant: str) -> object:
    raise ValueError(f"non-standard JSON constant: {constant}")


def _require_string(value: object, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string")
    if not allow_empty and not value.strip():
        raise ValidationError(f"{name} must not be empty")
    return value


def _parse_created_at(value: object) -> datetime:
    text = _require_string(value, "created_at")
    if not _UTC_TIMESTAMP.fullmatch(text):
        raise ValidationError("created_at must use canonical UTC RFC 3339 syntax")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as error:
        raise ValidationError("created_at must be a valid RFC 3339 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValidationError("created_at must be timezone-aware UTC")
    return parsed.astimezone(UTC)


def _validate_json(value: object, *, depth: int = 0) -> None:
    if depth > MAX_METADATA_DEPTH:
        raise ValidationError("metadata exceeds the maximum nesting depth")
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValidationError("metadata contains a non-finite number")
        return
    if isinstance(value, list):
        if depth >= MAX_METADATA_DEPTH:
            raise ValidationError("metadata exceeds the maximum nesting depth")
        for item in value:
            _validate_json(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if depth >= MAX_METADATA_DEPTH:
            raise ValidationError("metadata exceeds the maximum nesting depth")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ValidationError(
                    "metadata keys must be non-empty strings of at most 128 chars"
                )
            _validate_json(item, depth=depth + 1)
        return
    raise ValidationError("metadata must contain only JSON-compatible values")


def _copy_metadata(value: object) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("metadata must be an object")
    if len(value) > MAX_METADATA_KEYS:
        raise ValidationError("metadata contains too many keys")
    _validate_json(value)
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        encoded_bytes = encoded.encode("utf-8")
    except (UnicodeEncodeError, ValueError) as error:
        raise ValidationError("metadata is not safely JSON-serializable") from error
    if len(encoded_bytes) > MAX_METADATA_BYTES:
        raise ValidationError("metadata is too large")
    copied = cast(dict[str, Any], json.loads(encoded))
    return cast(Mapping[str, Any], _freeze_json(copied))


def _metadata_object(value: object) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("metadata must be an object")
    return cast(Mapping[str, Any], value)


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _utc_text(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    text = normalized.isoformat(timespec="microseconds")
    return text.removesuffix("+00:00") + "Z"


@dataclass(frozen=True, slots=True)
class NotificationRequest:
    """One durable notification occurrence under the stable schema v1 contract."""

    request_id: str
    idempotency_key: str = field(repr=False)
    provider: str
    subject: str = field(repr=False)
    title: str = field(repr=False)
    body: str = field(repr=False)
    severity: Severity
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError(f"schema_version must be {SCHEMA_VERSION!r}")
        if not _REQUEST_ID.fullmatch(self.request_id):
            raise ValidationError("request_id has an invalid format")
        if len(self.idempotency_key) > MAX_IDEMPOTENCY_KEY_CHARS or not _IDEMPOTENCY_KEY.fullmatch(
            self.idempotency_key
        ):
            raise ValidationError("idempotency_key has an invalid format")
        if not _PROVIDER.fullmatch(self.provider):
            raise ValidationError("provider has an invalid format")
        if len(self.subject) > MAX_SUBJECT_CHARS:
            raise ValidationError("subject is too long")
        if not self.title.strip() or len(self.title) > MAX_TITLE_CHARS:
            raise ValidationError("title must be non-empty and within the size limit")
        if not self.body.strip() or len(self.body) > MAX_BODY_CHARS:
            raise ValidationError("body must be non-empty and within the size limit")
        if self.severity not in SEVERITIES:
            raise ValidationError("severity is invalid")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValidationError("created_at must be timezone-aware")
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))
        object.__setattr__(self, "metadata", _copy_metadata(dict(self.metadata)))
        try:
            request_bytes = self.to_json().encode("utf-8")
        except (UnicodeEncodeError, ValueError) as error:
            raise ValidationError("request is not safely JSON-serializable") from error
        if len(request_bytes) > MAX_REQUEST_BYTES:
            raise ValidationError("serialized request is too large")

    @classmethod
    def from_dict(cls, value: object) -> NotificationRequest:
        """Validate an untrusted JSON-compatible value using strict field semantics."""
        if not isinstance(value, dict):
            raise ValidationError("request must be a JSON object")
        fields = set(value)
        missing = _EXPECTED_FIELDS - fields
        extra = fields - _EXPECTED_FIELDS
        if missing:
            raise ValidationError(f"request is missing fields: {', '.join(sorted(missing))}")
        if extra:
            raise ValidationError(f"request contains unknown fields: {', '.join(sorted(extra))}")
        severity = _require_string(value["severity"], "severity")
        if severity not in SEVERITIES:
            raise ValidationError("severity is invalid")
        return cls(
            schema_version=_require_string(value["schema_version"], "schema_version"),
            request_id=_require_string(value["request_id"], "request_id"),
            idempotency_key=_require_string(value["idempotency_key"], "idempotency_key"),
            provider=_require_string(value["provider"], "provider"),
            subject=_require_string(value["subject"], "subject", allow_empty=True),
            title=_require_string(value["title"], "title"),
            body=_require_string(value["body"], "body"),
            severity=cast(Severity, severity),
            metadata=_metadata_object(value["metadata"]),
            created_at=_parse_created_at(value["created_at"]),
        )

    @classmethod
    def from_json(cls, value: str) -> NotificationRequest:
        try:
            parsed: object = json.loads(
                value,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, ValueError) as error:
            raise ValidationError("request is not valid JSON") from error
        return cls.from_dict(parsed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "provider": self.provider,
            "subject": self.subject,
            "title": self.title,
            "body": self.body,
            "severity": self.severity,
            "metadata": _thaw_json(self.metadata),
            "created_at": _utc_text(self.created_at),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Opaque in-process result; provider evidence is never persisted or rendered."""

    provider: str = field(repr=False)
    message_id: str | None = field(default=None, repr=False)
    details: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class RequestStatus:
    """Public status projection that intentionally excludes notification content."""

    request_id: str
    provider: str
    state: str
    attempt_count: int
    next_attempt_at: str | None
    delivered_at: str | None
    dead_at: str | None
    last_error_code: str | None

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "request_id": self.request_id,
            "provider": self.provider,
            "state": self.state,
            "attempt_count": self.attempt_count,
            "next_attempt_at": self.next_attempt_at,
            "delivered_at": self.delivered_at,
            "dead_at": self.dead_at,
            "last_error_code": self.last_error_code,
        }
