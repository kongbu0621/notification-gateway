from __future__ import annotations

import sqlite3
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from conftest import make_request

from notification_gateway import (
    ConfigurationError,
    ConflictError,
    DeliveryError,
    DeliveryResult,
    DeliveryWorker,
    NotificationGateway,
    ProviderNotFoundError,
    RequestNotFoundError,
    RetryPolicy,
    SQLiteStore,
)
from notification_gateway.models import NotificationRequest


@dataclass
class FakeProvider:
    name: str = "fake"
    outcomes: list[object] | None = None
    received: list[NotificationRequest] | None = None

    def deliver(self, notification: NotificationRequest) -> DeliveryResult:
        if self.received is not None:
            self.received.append(notification)
        outcome = self.outcomes.pop(0) if self.outcomes else DeliveryResult(self.name, "msg-1")
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, DeliveryResult)
        return outcome


def gateway(tmp_path: Path, provider: FakeProvider | None = None) -> NotificationGateway:
    return NotificationGateway(
        SQLiteStore(tmp_path / "notifications.sqlite3"), [provider or FakeProvider()]
    )


def test_sqlite_file_permissions_are_owner_only(tmp_path: Path) -> None:
    db = tmp_path / "private" / "notifications.sqlite3"
    SQLiteStore(db)
    assert db.stat().st_mode & 0o777 == 0o600
    assert db.parent.stat().st_mode & 0o777 == 0o700


def test_v2_migration_redacts_legacy_provider_evidence(tmp_path: Path) -> None:
    db = tmp_path / "notifications.sqlite3"
    service = NotificationGateway(SQLiteStore(db), [FakeProvider()])
    service.accept(make_request(), now=0)
    claim = service.store.claim_due(now=0, lease_seconds=10)
    assert claim is not None
    connection = sqlite3.connect(db)
    connection.execute(
        """
        UPDATE notification_requests
        SET provider_message_id = 'legacy-secret',
            provider_details_json = '{"raw":"legacy-secret"}',
            last_error_code = 'legacy-secret', last_error_message = 'legacy-secret'
        """
    )
    connection.execute(
        """
        UPDATE delivery_attempts
        SET provider_message_id = 'legacy-secret',
            provider_details_json = '{"raw":"legacy-secret"}',
            error_code = 'legacy-secret', error_message = 'legacy-secret'
        """
    )
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()

    SQLiteStore(db)
    connection = sqlite3.connect(db)
    request_row = connection.execute(
        """
        SELECT provider_message_id, provider_details_json,
               last_error_code, last_error_message
        FROM notification_requests
        """
    ).fetchone()
    attempt_row = connection.execute(
        """
        SELECT provider_message_id, provider_details_json, error_code, error_message
        FROM delivery_attempts
        """
    ).fetchone()
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    connection.close()
    assert request_row == (None, None, "legacy_error_redacted", None)
    assert attempt_row == (None, None, "legacy_error_redacted", None)
    assert version == 2
    for database_file in tmp_path.glob("notifications.sqlite3*"):
        assert b"legacy-secret" not in database_file.read_bytes()


def test_future_database_schema_version_is_rejected(tmp_path: Path) -> None:
    db = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(db)
    connection.execute("PRAGMA user_version = 999")
    connection.close()

    with pytest.raises(ConfigurationError, match="newer than supported"):
        SQLiteStore(db)

    connection = sqlite3.connect(db)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 999
    connection.close()


def test_durable_idempotent_intake_and_key_reuse(tmp_path: Path) -> None:
    service = gateway(tmp_path)
    first = make_request()
    accepted = service.accept(first, now=10)
    replay = service.accept(first, now=20)
    second = make_request(request_id="demo-event-002")
    reused_key = service.accept(second, now=30)
    assert accepted.replayed is False
    assert replay.replayed is True
    assert reused_key.replayed is False
    assert service.status(first.request_id).state == "pending"

    with pytest.raises(ConflictError):
        service.accept(make_request(title="Different"), now=40)
    with pytest.raises(ValueError, match="finite"):
        service.accept(make_request(request_id="invalid-time"), now=float("inf"))


def test_unrepresentable_intake_time_is_rejected_without_commit(tmp_path: Path) -> None:
    service = gateway(tmp_path)
    with pytest.raises(ValueError, match="representable"):
        service.accept(make_request(), now=1e308)
    with pytest.raises(RequestNotFoundError):
        service.status("demo-event-001")


def test_unknown_provider_is_rejected_before_durable_intake(tmp_path: Path) -> None:
    service = gateway(tmp_path)
    with pytest.raises(ProviderNotFoundError):
        service.accept(make_request(provider="missing"), now=1)
    with pytest.raises(RequestNotFoundError):
        service.status("demo-event-001")


def test_success_marks_delivered_without_persisting_provider_evidence(tmp_path: Path) -> None:
    provider = FakeProvider(received=[])
    service = gateway(tmp_path, provider)
    service.accept(make_request(), now=1)
    assert DeliveryWorker(service).run_once(now=2)
    status = service.status("demo-event-001")
    assert status.state == "delivered"
    assert status.attempt_count == 1
    assert provider.received and provider.received[0].request_id == "demo-event-001"
    attempts = service.store.attempts_for_testing("demo-event-001")
    assert attempts[0]["outcome"] == "delivered"
    assert attempts[0]["provider_message_id"] is None
    assert attempts[0]["provider_details_json"] is None


def test_retry_backoff_survives_restart(tmp_path: Path) -> None:
    db = tmp_path / "notifications.sqlite3"
    failing = FakeProvider(
        outcomes=[DeliveryError("temporary", retryable=True, code="temporary_failure")]
    )
    service = NotificationGateway(SQLiteStore(db), [failing])
    service.accept(make_request(), now=10)
    worker = DeliveryWorker(
        service,
        RetryPolicy(max_attempts=3, base_delay_seconds=5, max_delay_seconds=20, lease_seconds=30),
    )
    assert worker.run_once(now=10)
    status = service.status("demo-event-001")
    assert status.state == "retry"
    assert status.next_attempt_at == "1970-01-01T00:00:15Z"

    restarted = NotificationGateway(SQLiteStore(db), [FakeProvider()])
    restarted_worker = DeliveryWorker(restarted, worker.policy)
    assert restarted_worker.run_once(now=14) is False
    assert restarted_worker.run_once(now=15) is True
    assert restarted.status("demo-event-001").state == "delivered"


def test_retry_exhaustion_and_permanent_failure(tmp_path: Path) -> None:
    retry = DeliveryError("later", retryable=True, code="temporary")
    provider = FakeProvider(outcomes=[retry, retry])
    service = gateway(tmp_path, provider)
    service.accept(make_request(), now=0)
    worker = DeliveryWorker(
        service,
        RetryPolicy(max_attempts=2, base_delay_seconds=1, max_delay_seconds=2, lease_seconds=5),
    )
    assert worker.run_once(now=0)
    assert worker.run_once(now=1)
    status = service.status("demo-event-001")
    assert status.state == "dead"
    assert status.attempt_count == 2

    second = make_request(request_id="demo-event-002")
    service.register(
        FakeProvider(outcomes=[DeliveryError("no", retryable=False, code="permanent")]),
        replace=True,
    )
    service.accept(second, now=2)
    assert worker.run_once(now=2)
    assert service.status(second.request_id).state == "dead"


def test_crash_lease_recovery_reuses_request_id(tmp_path: Path) -> None:
    service = gateway(tmp_path)
    service.accept(make_request(), now=0)
    first_claim = service.store.claim_due(now=0, lease_seconds=10)
    assert first_claim is not None
    assert first_claim.notification.request_id == "demo-event-001"
    assert service.store.claim_due(now=9, lease_seconds=10) is None
    recovered = service.store.claim_due(now=10, lease_seconds=10)
    assert recovered is not None
    assert recovered.notification.request_id == first_claim.notification.request_id
    assert recovered.attempt_no == 2
    attempts = service.store.attempts_for_testing(first_claim.notification.request_id)
    assert attempts[0]["outcome"] == "retry"
    assert attempts[0]["error_code"] == "lease_expired"
    assert attempts[0]["finished_at"] == 10


def test_crash_lease_recovery_honors_max_attempts(tmp_path: Path) -> None:
    service = gateway(tmp_path)
    service.accept(make_request(), now=0)
    first = service.store.claim_due(now=0, lease_seconds=1, max_attempts=2)
    assert first is not None and first.attempt_no == 1
    second = service.store.claim_due(now=1, lease_seconds=1, max_attempts=2)
    assert second is not None and second.attempt_no == 2
    assert service.store.claim_due(now=2, lease_seconds=1, max_attempts=2) is None
    status = service.status("demo-event-001")
    assert status.state == "dead"
    assert status.attempt_count == 2
    assert status.last_error_code == "lease_expired_exhausted"
    attempts = service.store.attempts_for_testing("demo-event-001")
    assert [attempt["outcome"] for attempt in attempts] == ["retry", "dead"]


def test_claim_rejects_invalid_attempt_and_time_configuration(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "notifications.sqlite3")
    with pytest.raises(ValueError, match="max_attempts"):
        store.claim_due(now=0, lease_seconds=1, max_attempts=0)
    with pytest.raises(ValueError, match="finite"):
        store.claim_due(now=float("nan"), lease_seconds=1)
    with pytest.raises(ValueError, match="finite"):
        store.claim_due(now=cast(Any, "not-a-number"), lease_seconds=1)
    with pytest.raises(ValueError, match="finite"):
        store.claim_due(now=10**5000, lease_seconds=1)


def test_unrepresentable_lease_is_rejected_without_state_change(tmp_path: Path) -> None:
    service = gateway(tmp_path)
    service.accept(make_request(), now=0)
    with pytest.raises(ValueError, match="lease deadline"):
        service.store.claim_due(now=200_000_000_000, lease_seconds=100_000_000_000)
    status = service.status("demo-event-001")
    assert status.state == "pending"
    assert status.attempt_count == 0
    assert service.store.attempts_for_testing("demo-event-001") == []


def test_store_rejects_non_gateway_failure_evidence(tmp_path: Path) -> None:
    service = gateway(tmp_path)
    service.accept(make_request(), now=0)
    claim = service.store.claim_due(now=0, lease_seconds=1)
    assert claim is not None
    with pytest.raises(ValueError, match="gateway-owned"):
        service.store.mark_failed(
            claim.notification.request_id,
            claim.attempt_no,
            retryable=False,
            exhausted=True,
            error_code="secretcredential123",
            error_message="secret diagnostic",
            now=0,
            retry_at=None,
        )
    with pytest.raises(ValueError, match="finite"):
        service.store.mark_delivered(
            claim.notification.request_id,
            claim.attempt_no,
            DeliveryResult("fake"),
            now=float("nan"),
        )
    with pytest.raises(ValueError, match="now"):
        service.store.mark_failed(
            claim.notification.request_id,
            claim.attempt_no,
            retryable=False,
            exhausted=True,
            error_code="provider_delivery_error",
            error_message="provider reported a delivery failure",
            now=float("inf"),
            retry_at=None,
        )
    with pytest.raises(ValueError, match="must not have"):
        service.store.mark_failed(
            claim.notification.request_id,
            claim.attempt_no,
            retryable=False,
            exhausted=True,
            error_code="provider_delivery_error",
            error_message="provider reported a delivery failure",
            now=0,
            retry_at=1,
        )
    with pytest.raises(ValueError, match="require a finite"):
        service.store.mark_failed(
            claim.notification.request_id,
            claim.attempt_no,
            retryable=True,
            exhausted=False,
            error_code="provider_delivery_error",
            error_message="provider reported a delivery failure",
            now=0,
            retry_at=None,
        )
    with pytest.raises(ValueError, match="representable"):
        service.store.mark_failed(
            claim.notification.request_id,
            claim.attempt_no,
            retryable=True,
            exhausted=False,
            error_code="provider_delivery_error",
            error_message="provider reported a delivery failure",
            now=0,
            retry_at=1e308,
        )


def test_provider_io_occurs_outside_write_transaction(tmp_path: Path) -> None:
    db = tmp_path / "notifications.sqlite3"

    @dataclass
    class TransactionProbe:
        name: str = "fake"

        def deliver(self, notification: NotificationRequest) -> DeliveryResult:
            connection = sqlite3.connect(db, timeout=0.2)
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE notification_requests SET provider = provider WHERE request_id = ?",
                    (notification.request_id,),
                )
                connection.commit()
            finally:
                connection.close()
            return DeliveryResult(self.name)

    service = NotificationGateway(SQLiteStore(db), [TransactionProbe()])
    service.accept(make_request(), now=0)
    assert DeliveryWorker(service).run_once(now=0)


def test_unexpected_provider_secret_is_not_persisted(tmp_path: Path) -> None:
    secret = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=never-store-me"
    service = gateway(tmp_path, FakeProvider(outcomes=[RuntimeError(secret)]))
    service.accept(make_request(), now=0)
    worker = DeliveryWorker(
        service,
        RetryPolicy(max_attempts=1, base_delay_seconds=1, max_delay_seconds=1),
    )
    assert worker.run_once(now=0)
    status = service.status("demo-event-001")
    assert status.last_error_code == "unexpected_provider_error"
    attempts = service.store.attempts_for_testing("demo-event-001")
    assert secret not in repr(attempts)
    assert "never-store-me" not in (tmp_path / "notifications.sqlite3").read_bytes().decode(
        "utf-8", errors="ignore"
    )


def test_provider_secret_is_not_exception_context_when_persistence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "provider-secret-never-log"
    service = gateway(tmp_path, FakeProvider(outcomes=[RuntimeError(secret)]))
    service.accept(make_request(), now=0)

    def fail_persistence(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("simulated database failure")

    monkeypatch.setattr(service.store, "mark_failed", fail_persistence)
    with pytest.raises(sqlite3.OperationalError) as raised:
        DeliveryWorker(service).run_once(now=0)

    assert raised.value.__context__ is None
    assert secret not in "".join(traceback.format_exception(raised.value))


def test_unrepresentable_retry_horizon_is_rejected_before_claim_or_provider_io(
    tmp_path: Path,
) -> None:
    received: list[NotificationRequest] = []
    service = gateway(tmp_path, FakeProvider(received=received))
    service.accept(make_request(), now=0)
    worker = DeliveryWorker(
        service,
        RetryPolicy(base_delay_seconds=1e308, max_delay_seconds=1e308),
    )
    with pytest.raises(ValueError, match="retry deadline"):
        worker.run_once(now=0)
    assert received == []
    assert service.status("demo-event-001").state == "pending"


def test_declared_delivery_error_text_is_not_persisted(tmp_path: Path) -> None:
    secret = "https://example.invalid/hook?token=never-store-me"
    error = DeliveryError(secret, retryable=False, code="provider_rejected")
    assert secret not in repr(error)
    assert secret not in "".join(traceback.format_exception(error))
    service = gateway(
        tmp_path,
        FakeProvider(outcomes=[error]),
    )
    service.accept(make_request(), now=0)
    assert DeliveryWorker(service).run_once(now=0)
    attempts = service.store.attempts_for_testing("demo-event-001")
    assert attempts[0]["error_message"] == "provider reported a delivery failure"
    assert attempts[0]["error_code"] == "provider_delivery_error"
    assert secret not in repr(attempts)
    assert "never-store-me" not in (tmp_path / "notifications.sqlite3").read_bytes().decode(
        "utf-8", errors="ignore"
    )


def test_mismatched_provider_result_becomes_dead_without_retry(tmp_path: Path) -> None:
    service = gateway(
        tmp_path,
        FakeProvider(outcomes=[DeliveryResult("other", "message-1", {"accepted": True})]),
    )
    service.accept(make_request(), now=0)
    assert DeliveryWorker(service).run_once(now=0)
    status = service.status("demo-event-001")
    assert status.state == "dead"
    assert status.last_error_code == "invalid_provider_result"
    attempts = service.store.attempts_for_testing("demo-event-001")
    assert attempts[0]["outcome"] == "dead"
    assert attempts[0]["provider_message_id"] is None


def test_provider_identifiers_details_and_codes_never_persist(tmp_path: Path) -> None:
    secret_id = "gh" + "p_" + "a" * 40
    secret_detail = "https://example.invalid/provider?token=never-store"
    provider = FakeProvider(
        outcomes=[
            DeliveryResult(
                "fake",
                secret_id,
                {"raw_response": secret_detail, "huge": 10**5000},
            )
        ]
    )
    service = gateway(tmp_path, provider)
    service.accept(make_request(), now=0)
    assert DeliveryWorker(service).run_once(now=0)
    database = (tmp_path / "notifications.sqlite3").read_bytes()
    assert secret_id.encode() not in database
    assert secret_detail.encode() not in database
    assert service.status("demo-event-001").state == "delivered"

    second = make_request(request_id="demo-event-002")
    service.register(
        FakeProvider(
            outcomes=[
                DeliveryError("ignored diagnostic", retryable=False, code="secretcredential123")
            ]
        ),
        replace=True,
    )
    service.accept(second, now=1)
    assert DeliveryWorker(service).run_once(now=1)
    assert service.status(second.request_id).last_error_code == "provider_delivery_error"
    assert b"secretcredential123" not in (tmp_path / "notifications.sqlite3").read_bytes()


def test_non_result_provider_output_becomes_dead_without_retry(tmp_path: Path) -> None:
    @dataclass
    class InvalidProvider:
        name: str = "fake"

        def deliver(self, notification: NotificationRequest) -> DeliveryResult:
            return cast(Any, None)

    service = NotificationGateway(
        SQLiteStore(tmp_path / "notifications.sqlite3"), [InvalidProvider()]
    )
    service.accept(make_request(), now=0)
    assert DeliveryWorker(service).run_once(now=0)
    status = service.status("demo-event-001")
    assert status.state == "dead"
    assert status.last_error_code == "invalid_provider_result"


def test_provider_removed_after_intake_becomes_dead(tmp_path: Path) -> None:
    db = tmp_path / "notifications.sqlite3"
    initial = NotificationGateway(SQLiteStore(db), [FakeProvider()])
    initial.accept(make_request(), now=0)
    restarted_without_provider = NotificationGateway(SQLiteStore(db))
    assert DeliveryWorker(restarted_without_provider).run_once(now=0)
    status = restarted_without_provider.status("demo-event-001")
    assert status.state == "dead"
    assert status.last_error_code == "unknown_provider"


def test_terminal_purge_cascades_attempts_but_preserves_pending(tmp_path: Path) -> None:
    service = gateway(tmp_path)
    delivered = make_request()
    pending = make_request(request_id="demo-event-pending")
    service.accept(delivered, now=0)
    service.accept(pending, now=0)
    assert DeliveryWorker(service).run_once(now=1)
    assert (
        service.store.purge_terminal(
            now=11, delivered_retention_seconds=10, dead_retention_seconds=10
        )
        == 1
    )
    with pytest.raises(RequestNotFoundError):
        service.status(delivered.request_id)
    assert service.store.attempts_for_testing(delivered.request_id) == []
    assert service.status(pending.request_id).state == "pending"
    with pytest.raises(ValueError):
        service.store.purge_terminal(
            now=1, delivered_retention_seconds=-1, dead_retention_seconds=1
        )
    with pytest.raises(ValueError, match="finite"):
        service.store.purge_terminal(
            now=1, delivered_retention_seconds=float("nan"), dead_retention_seconds=1
        )
    with pytest.raises(ValueError, match="finite"):
        service.store.purge_terminal(
            now=float("inf"), delivered_retention_seconds=1, dead_retention_seconds=1
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"base_delay_seconds": 0},
        {"max_delay_seconds": 0},
        {"base_delay_seconds": 2, "max_delay_seconds": 1},
        {"lease_seconds": 0},
        {"base_delay_seconds": float("nan")},
        {"lease_seconds": float("inf")},
        {"max_attempts": True},
        {"lease_seconds": 10**5000},
        {"lease_seconds": cast(Any, "60")},
    ],
)
def test_retry_policy_validation(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


def test_retry_policy_rejects_invalid_attempt_and_caps_delay() -> None:
    policy = RetryPolicy(base_delay_seconds=2, max_delay_seconds=3)
    with pytest.raises(ValueError, match="attempt_no"):
        policy.delay_for(0)
    assert policy.delay_for(2) == 3
    assert policy.delay_for(1_000_000) == 3
    wide = RetryPolicy(base_delay_seconds=1, max_delay_seconds=1e100)
    assert wide.delay_for(65) == 2.0**64
    below_power = RetryPolicy(base_delay_seconds=0.9999999999999999, max_delay_seconds=2.0**64)
    assert below_power.delay_for(65) == 18_446_744_073_709_549_568
    overflow = RetryPolicy(base_delay_seconds=1e308, max_delay_seconds=1.7e308)
    assert overflow.delay_for(2) == 1.7e308
    assert RetryPolicy(base_delay_seconds=3, max_delay_seconds=3).delay_for(1) == 3


def test_provider_registry_errors(tmp_path: Path) -> None:
    service = gateway(tmp_path)
    with pytest.raises(Exception, match="already registered"):
        service.register(FakeProvider())
    service.register(FakeProvider(), replace=True)
    with pytest.raises(Exception, match="implement"):
        service.register(object())  # type: ignore[arg-type]
