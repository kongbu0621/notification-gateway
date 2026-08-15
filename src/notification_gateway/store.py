"""Short-transaction SQLite persistence for durable notification delivery."""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, cast

from .exceptions import ConfigurationError, ConflictError, RequestNotFoundError
from .models import DeliveryResult, NotificationRequest, RequestStatus

_SCHEMA: Final = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS notification_requests (
    request_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    provider TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending','in_flight','retry','delivered','dead')),
    accepted_at REAL NOT NULL,
    next_attempt_at REAL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_until REAL,
    delivered_at REAL,
    dead_at REAL,
    last_error_code TEXT,
    last_error_message TEXT,
    provider_message_id TEXT,
    provider_details_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_requests_due
    ON notification_requests(state, next_attempt_at, lease_until);
CREATE INDEX IF NOT EXISTS idx_requests_terminal
    ON notification_requests(state, delivered_at, dead_at);
CREATE TABLE IF NOT EXISTS delivery_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL REFERENCES notification_requests(request_id) ON DELETE CASCADE,
    attempt_no INTEGER NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    outcome TEXT CHECK (outcome IN ('delivered','retry','dead')),
    retryable INTEGER,
    error_code TEXT,
    error_message TEXT,
    provider_message_id TEXT,
    provider_details_json TEXT,
    UNIQUE(request_id, attempt_no)
);
"""
_SCHEMA_VERSION: Final = 2
_PERSISTED_FAILURES: Final = {
    "unknown_provider": "configured provider is unavailable",
    "provider_delivery_error": "provider reported a delivery failure",
    "unexpected_provider_error": "provider raised an unexpected error",
    "invalid_provider_result": "provider returned invalid delivery evidence",
    "invalid_stored_payload": "stored notification payload failed integrity validation",
}
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


def _is_finite_number(value: object) -> bool:
    if type(value) not in {int, float}:
        return False
    try:
        return math.isfinite(value)  # type: ignore[arg-type]
    except OverflowError:
        return False


def _timestamp_datetime(value: float) -> datetime:
    return _EPOCH + timedelta(seconds=value)


def _require_timestamp(value: object, name: str) -> float:
    if not _is_finite_number(value):
        raise ValueError(f"{name} must be a finite, representable Unix timestamp")
    normalized = float(cast(int | float, value))
    try:
        _timestamp_datetime(normalized)
    except (OverflowError, ValueError):
        raise ValueError(f"{name} must be a finite, representable Unix timestamp") from None
    return normalized


def _require_duration(value: object, name: str, *, positive: bool) -> float:
    if not _is_finite_number(value):
        raise ValueError(f"{name} must be a finite number")
    normalized = float(cast(int | float, value))
    invalid = normalized <= 0 if positive else normalized < 0
    if invalid:
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be {qualifier}")
    return normalized


def _add_timestamp(timestamp: float, duration: float, name: str) -> float:
    return _require_timestamp(timestamp + duration, name)


def _iso_or_none(value: object) -> str | None:
    if value is None:
        return None
    timestamp = _require_timestamp(value, "stored timestamp")
    return _timestamp_datetime(timestamp).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    status: RequestStatus
    replayed: bool


@dataclass(frozen=True, slots=True)
class ClaimedRequest:
    notification: NotificationRequest
    attempt_no: int


class SQLiteStore:
    """Durable store whose methods hold SQLite write locks only for bookkeeping."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            target = Path(self.path)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._secure_database_files()
        self._memory_connection: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory_connection = self._new_connection()
        connection = self._connect()
        try:
            self._reject_future_schema(connection)
            connection.executescript(_SCHEMA)
            self._migrate(connection)
        finally:
            if self._memory_connection is None:
                connection.close()
        if self.path != ":memory:":
            self._secure_database_files()

    def _secure_database_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            database_file = Path(f"{self.path}{suffix}")
            try:
                os.chmod(database_file, 0o600)
            except FileNotFoundError:
                continue

    @staticmethod
    def _reject_future_schema(connection: sqlite3.Connection) -> None:
        version = cast(int, connection.execute("PRAGMA user_version").fetchone()[0])
        if version > _SCHEMA_VERSION:
            raise ConfigurationError("database schema version is newer than supported")

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA secure_delete = ON")
            self._reject_future_schema(connection)
            if self.path != ":memory:":
                self._secure_database_files()
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
                self._secure_database_files()
            return connection
        except Exception:
            connection.close()
            raise

    def _connect(self) -> sqlite3.Connection:
        if self._memory_connection is not None:
            return self._memory_connection
        return self._new_connection()

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        version = cast(int, connection.execute("PRAGMA user_version").fetchone()[0])
        if version > _SCHEMA_VERSION:
            raise ConfigurationError("database schema version is newer than supported")
        if version == _SCHEMA_VERSION:
            return
        connection.execute("BEGIN IMMEDIATE")
        try:
            # v2 stops persisting provider-controlled delivery evidence. Redact
            # legacy rows once so upgrading does not leave old secrets behind.
            connection.execute(
                """
                UPDATE notification_requests
                SET provider_message_id = NULL, provider_details_json = NULL,
                    last_error_code = CASE WHEN last_error_code IS NULL THEN NULL
                        ELSE 'legacy_error_redacted' END,
                    last_error_message = NULL
                """
            )
            connection.execute(
                """
                UPDATE delivery_attempts
                SET provider_message_id = NULL, provider_details_json = NULL,
                    error_code = CASE WHEN error_code IS NULL THEN NULL
                        ELSE 'legacy_error_redacted' END,
                    error_message = NULL
                """
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        if connection.execute("PRAGMA database_list").fetchone()[2] != "":
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("VACUUM")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        # Set the version only after physical cleanup succeeds. A failed
        # checkpoint/VACUUM therefore retries the fail-closed migration later.
        connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    @staticmethod
    def _notification_from_row(row: sqlite3.Row) -> NotificationRequest | None:
        payload = row["payload_json"]
        stored_hash = row["payload_hash"]
        if type(payload) is not str or type(stored_hash) is not str:
            return None
        try:
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            notification = NotificationRequest.from_json(payload)
        except (TypeError, ValueError, OverflowError, UnicodeError):
            return None
        if not stored_hash.isascii() or not hmac.compare_digest(digest, stored_hash):
            return None
        if (
            notification.request_id != row["request_id"]
            or notification.idempotency_key != row["idempotency_key"]
            or notification.provider != row["provider"]
        ):
            return None
        return notification

    @staticmethod
    def _begin(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _finish(connection: sqlite3.Connection, *, ok: bool) -> None:
        connection.execute("COMMIT" if ok else "ROLLBACK")

    @staticmethod
    def _status_from_row(row: sqlite3.Row) -> RequestStatus:
        return RequestStatus(
            request_id=cast(str, row["request_id"]),
            provider=cast(str, row["provider"]),
            state=cast(str, row["state"]),
            attempt_count=cast(int, row["attempt_count"]),
            next_attempt_at=_iso_or_none(row["next_attempt_at"]),
            delivered_at=_iso_or_none(row["delivered_at"]),
            dead_at=_iso_or_none(row["dead_at"]),
            last_error_code=cast(str | None, row["last_error_code"]),
        )

    def enqueue(self, notification: NotificationRequest, *, now: float) -> EnqueueResult:
        now = _require_timestamp(now, "now")
        payload = notification.to_json()
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        connection = self._connect()
        ok = False
        try:
            self._begin(connection)
            existing = connection.execute(
                "SELECT * FROM notification_requests WHERE request_id = ?",
                (notification.request_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != digest or existing["payload_json"] != payload:
                    raise ConflictError("request_id already exists with a different payload")
                ok = True
                self._finish(connection, ok=True)
                return EnqueueResult(self._status_from_row(existing), replayed=True)
            connection.execute(
                """
                INSERT INTO notification_requests (
                    request_id, payload_json, payload_hash, idempotency_key, provider,
                    state, accepted_at, next_attempt_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    notification.request_id,
                    payload,
                    digest,
                    notification.idempotency_key,
                    notification.provider,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM notification_requests WHERE request_id = ?",
                (notification.request_id,),
            ).fetchone()
            if row is None:  # Defensive: the insert and read share one write transaction.
                raise RuntimeError("inserted notification request could not be read")
            ok = True
            self._finish(connection, ok=True)
            return EnqueueResult(self._status_from_row(row), replayed=False)
        except Exception:
            if not ok and connection.in_transaction:
                self._finish(connection, ok=False)
            raise
        finally:
            if self._memory_connection is None:
                connection.close()

    def get_status(self, request_id: str) -> RequestStatus:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM notification_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise RequestNotFoundError("notification request was not found")
            return self._status_from_row(row)
        finally:
            if self._memory_connection is None:
                connection.close()

    def claim_due(
        self, *, now: float, lease_seconds: float, max_attempts: int = 5
    ) -> ClaimedRequest | None:
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        now = _require_timestamp(now, "now")
        lease_seconds = _require_duration(lease_seconds, "lease_seconds", positive=True)
        lease_until = _add_timestamp(now, lease_seconds, "lease deadline")
        connection = self._connect()
        ok = False
        try:
            self._begin(connection)
            connection.execute(
                """
                UPDATE delivery_attempts
                SET finished_at = ?,
                    outcome = CASE WHEN (
                        SELECT request.attempt_count
                        FROM notification_requests AS request
                        WHERE request.request_id = delivery_attempts.request_id
                    ) >= ? THEN 'dead' ELSE 'retry' END,
                    retryable = CASE WHEN (
                        SELECT request.attempt_count
                        FROM notification_requests AS request
                        WHERE request.request_id = delivery_attempts.request_id
                    ) >= ? THEN 0 ELSE 1 END,
                    error_code = CASE WHEN (
                        SELECT request.attempt_count
                        FROM notification_requests AS request
                        WHERE request.request_id = delivery_attempts.request_id
                    ) >= ? THEN 'lease_expired_exhausted' ELSE 'lease_expired' END,
                    error_message = CASE WHEN (
                        SELECT request.attempt_count
                        FROM notification_requests AS request
                        WHERE request.request_id = delivery_attempts.request_id
                    ) >= ? THEN 'delivery attempts exhausted after lease expiry'
                    ELSE 'previous delivery lease expired' END
                WHERE outcome IS NULL AND EXISTS (
                    SELECT 1 FROM notification_requests AS request
                    WHERE request.request_id = delivery_attempts.request_id
                      AND request.state = 'in_flight'
                      AND request.lease_until <= ?
                )
                """,
                (now, max_attempts, max_attempts, max_attempts, max_attempts, now),
            )
            connection.execute(
                """
                UPDATE notification_requests
                SET state = CASE WHEN attempt_count >= ? THEN 'dead' ELSE 'retry' END,
                    next_attempt_at = CASE WHEN attempt_count >= ? THEN NULL ELSE ? END,
                    lease_until = NULL,
                    dead_at = CASE WHEN attempt_count >= ? THEN ? ELSE NULL END,
                    last_error_code = CASE WHEN attempt_count >= ?
                        THEN 'lease_expired_exhausted' ELSE 'lease_expired' END,
                    last_error_message = CASE WHEN attempt_count >= ?
                        THEN 'delivery attempts exhausted after lease expiry'
                        ELSE 'previous delivery lease expired' END
                WHERE state = 'in_flight' AND lease_until <= ?
                """,
                (
                    max_attempts,
                    max_attempts,
                    now,
                    max_attempts,
                    now,
                    max_attempts,
                    max_attempts,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE notification_requests
                SET state = 'dead', next_attempt_at = NULL, lease_until = NULL,
                    dead_at = ?, last_error_code = 'attempts_exhausted',
                    last_error_message = 'delivery attempts exhausted'
                WHERE state IN ('pending','retry') AND attempt_count >= ?
                """,
                (now, max_attempts),
            )
            row = connection.execute(
                """
                SELECT * FROM notification_requests
                WHERE state IN ('pending','retry') AND attempt_count < ? AND next_attempt_at <= ?
                ORDER BY next_attempt_at, accepted_at, request_id
                LIMIT 1
                """,
                (max_attempts, now),
            ).fetchone()
            if row is None:
                ok = True
                self._finish(connection, ok=True)
                return None
            request_id = cast(str, row["request_id"])
            notification = self._notification_from_row(row)
            if notification is None:
                connection.execute(
                    """
                    UPDATE notification_requests
                    SET state = 'dead', next_attempt_at = NULL, lease_until = NULL,
                        dead_at = ?, last_error_code = 'invalid_stored_payload',
                        last_error_message = ?
                    WHERE request_id = ?
                    """,
                    (now, _PERSISTED_FAILURES["invalid_stored_payload"], request_id),
                )
                ok = True
                self._finish(connection, ok=True)
                return None
            attempt_no = cast(int, row["attempt_count"]) + 1
            connection.execute(
                """
                UPDATE notification_requests
                SET state = 'in_flight', attempt_count = ?, lease_until = ?, next_attempt_at = NULL
                WHERE request_id = ?
                """,
                (attempt_no, lease_until, request_id),
            )
            connection.execute(
                """
                INSERT INTO delivery_attempts(request_id, attempt_no, started_at)
                VALUES (?, ?, ?)
                """,
                (request_id, attempt_no, now),
            )
            ok = True
            self._finish(connection, ok=True)
            return ClaimedRequest(notification, attempt_no)
        except Exception:
            if not ok and connection.in_transaction:
                self._finish(connection, ok=False)
            raise
        finally:
            if self._memory_connection is None:
                connection.close()

    def mark_delivered(
        self,
        request_id: str,
        attempt_no: int,
        result: DeliveryResult,
        *,
        now: float,
    ) -> None:
        now = _require_timestamp(now, "now")
        connection = self._connect()
        try:
            self._begin(connection)
            cursor = connection.execute(
                """
                UPDATE notification_requests
                SET state = 'delivered', lease_until = NULL, next_attempt_at = NULL,
                    delivered_at = ?, dead_at = NULL, last_error_code = NULL,
                    last_error_message = NULL, provider_message_id = ?, provider_details_json = ?
                WHERE request_id = ? AND state = 'in_flight' AND attempt_count = ?
                """,
                (now, None, None, request_id, attempt_no),
            )
            if cursor.rowcount != 1:
                raise RequestNotFoundError("active delivery attempt was not found")
            connection.execute(
                """
                UPDATE delivery_attempts
                SET finished_at = ?, outcome = 'delivered', retryable = 0,
                    provider_message_id = ?, provider_details_json = ?
                WHERE request_id = ? AND attempt_no = ?
                """,
                (now, None, None, request_id, attempt_no),
            )
            self._finish(connection, ok=True)
        except Exception:
            if connection.in_transaction:
                self._finish(connection, ok=False)
            raise
        finally:
            if self._memory_connection is None:
                connection.close()

    def mark_failed(
        self,
        request_id: str,
        attempt_no: int,
        *,
        retryable: bool,
        exhausted: bool,
        error_code: str,
        error_message: str,
        now: float,
        retry_at: float | None,
    ) -> None:
        if _PERSISTED_FAILURES.get(error_code) != error_message:
            raise ValueError("failure code and message must be gateway-owned")
        terminal = exhausted or not retryable
        now = _require_timestamp(now, "now")
        if terminal:
            if retry_at is not None:
                raise ValueError("terminal failures must not have a retry time")
        elif retry_at is None:
            raise ValueError("retryable failures require a finite, representable retry time")
        else:
            retry_at = _require_timestamp(retry_at, "retry time")
        state = "dead" if terminal else "retry"
        outcome = "dead" if terminal else "retry"
        connection = self._connect()
        try:
            self._begin(connection)
            cursor = connection.execute(
                """
                UPDATE notification_requests
                SET state = ?, lease_until = NULL, next_attempt_at = ?,
                    dead_at = ?, last_error_code = ?, last_error_message = ?
                WHERE request_id = ? AND state = 'in_flight' AND attempt_count = ?
                """,
                (
                    state,
                    None if terminal else retry_at,
                    now if terminal else None,
                    error_code,
                    error_message,
                    request_id,
                    attempt_no,
                ),
            )
            if cursor.rowcount != 1:
                raise RequestNotFoundError("active delivery attempt was not found")
            connection.execute(
                """
                UPDATE delivery_attempts
                SET finished_at = ?, outcome = ?, retryable = ?, error_code = ?, error_message = ?
                WHERE request_id = ? AND attempt_no = ?
                """,
                (
                    now,
                    outcome,
                    int(retryable),
                    error_code,
                    error_message,
                    request_id,
                    attempt_no,
                ),
            )
            self._finish(connection, ok=True)
        except Exception:
            if connection.in_transaction:
                self._finish(connection, ok=False)
            raise
        finally:
            if self._memory_connection is None:
                connection.close()

    def purge_terminal(
        self,
        *,
        now: float,
        delivered_retention_seconds: float,
        dead_retention_seconds: float,
    ) -> int:
        now = _require_timestamp(now, "now")
        delivered_retention_seconds = _require_duration(
            delivered_retention_seconds, "delivered retention", positive=False
        )
        dead_retention_seconds = _require_duration(
            dead_retention_seconds, "dead retention", positive=False
        )
        connection = self._connect()
        try:
            self._begin(connection)
            cursor = connection.execute(
                """
                DELETE FROM notification_requests
                WHERE (state = 'delivered' AND delivered_at <= ?)
                   OR (state = 'dead' AND dead_at <= ?)
                """,
                (now - delivered_retention_seconds, now - dead_retention_seconds),
            )
            count = cursor.rowcount
            self._finish(connection, ok=True)
            return count
        except Exception:
            if connection.in_transaction:
                self._finish(connection, ok=False)
            raise
        finally:
            if self._memory_connection is None:
                connection.close()

    def attempts_for_testing(self, request_id: str) -> list[dict[str, Any]]:
        """Return audit rows for tests; not part of the public package API."""
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM delivery_attempts WHERE request_id = ? ORDER BY attempt_no",
                (request_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            if self._memory_connection is None:
                connection.close()
