from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import make_request

from notification_gateway import (
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


def test_unknown_provider_is_rejected_before_durable_intake(tmp_path: Path) -> None:
    service = gateway(tmp_path)
    with pytest.raises(ProviderNotFoundError):
        service.accept(make_request(provider="missing"), now=1)
    with pytest.raises(RequestNotFoundError):
        service.status("demo-event-001")


def test_success_marks_delivered_and_persists_evidence(tmp_path: Path) -> None:
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
    assert attempts[0]["provider_message_id"] == "msg-1"


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


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"base_delay_seconds": 0},
        {"max_delay_seconds": 0},
        {"base_delay_seconds": 2, "max_delay_seconds": 1},
        {"lease_seconds": 0},
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


def test_provider_registry_errors(tmp_path: Path) -> None:
    service = gateway(tmp_path)
    with pytest.raises(Exception, match="already registered"):
        service.register(FakeProvider())
    service.register(FakeProvider(), replace=True)
    with pytest.raises(Exception, match="implement"):
        service.register(object())  # type: ignore[arg-type]
