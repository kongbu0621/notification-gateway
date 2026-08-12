"""At-least-once worker with bounded retry and crash-lease recovery."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Final

from .exceptions import DeliveryError, ProviderNotFoundError
from .gateway import NotificationGateway

_SAFE_CODE: Final = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_MAX_ERROR_CHARS: Final = 160


def _safe_code(value: str) -> str:
    return value if _SAFE_CODE.fullmatch(value) else "delivery_error"


def _safe_message(value: str) -> str:
    sanitized = " ".join(value.replace("\x00", "").split())
    return sanitized[:_MAX_ERROR_CHARS] or "notification delivery failed"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay_seconds: float = 5.0
    max_delay_seconds: float = 300.0
    lease_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.base_delay_seconds <= 0 or self.max_delay_seconds <= 0:
            raise ValueError("retry delays must be positive")
        if self.base_delay_seconds > self.max_delay_seconds:
            raise ValueError("base retry delay must not exceed maximum delay")
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

    def delay_for(self, attempt_no: int) -> float:
        if attempt_no < 1:
            raise ValueError("attempt_no must be at least one")
        delay = self.base_delay_seconds * (2.0 ** (attempt_no - 1))
        return min(self.max_delay_seconds, delay)


class DeliveryWorker:
    """Claim one request, perform I/O outside transactions, then persist the outcome."""

    def __init__(self, gateway: NotificationGateway, policy: RetryPolicy | None = None) -> None:
        self.gateway = gateway
        self.policy = RetryPolicy() if policy is None else policy

    def run_once(self, *, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        claim = self.gateway.store.claim_due(
            now=current,
            lease_seconds=self.policy.lease_seconds,
        )
        if claim is None:
            return False

        request = claim.notification
        try:
            provider = self.gateway.provider(request.provider)
            result = provider.deliver(request)
        except ProviderNotFoundError:
            self.gateway.store.mark_failed(
                request.request_id,
                claim.attempt_no,
                retryable=False,
                exhausted=True,
                error_code="unknown_provider",
                error_message="configured provider is unavailable",
                now=current,
                retry_at=None,
            )
        except DeliveryError as error:
            exhausted = claim.attempt_no >= self.policy.max_attempts
            retry_at = current + self.policy.delay_for(claim.attempt_no)
            self.gateway.store.mark_failed(
                request.request_id,
                claim.attempt_no,
                retryable=error.retryable,
                exhausted=exhausted,
                error_code=_safe_code(error.code),
                error_message=_safe_message(str(error)),
                now=current,
                retry_at=retry_at,
            )
        except Exception:
            exhausted = claim.attempt_no >= self.policy.max_attempts
            retry_at = current + self.policy.delay_for(claim.attempt_no)
            self.gateway.store.mark_failed(
                request.request_id,
                claim.attempt_no,
                retryable=True,
                exhausted=exhausted,
                error_code="unexpected_provider_error",
                error_message="provider raised an unexpected error",
                now=current,
                retry_at=retry_at,
            )
        else:
            self.gateway.store.mark_delivered(
                request.request_id,
                claim.attempt_no,
                result,
                now=current,
            )
        return True
