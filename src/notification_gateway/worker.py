"""At-least-once worker with bounded retry and crash-lease recovery."""

from __future__ import annotations

import time
from dataclasses import dataclass
from math import isfinite, ldexp
from typing import Final

from .exceptions import DeliveryError, ProviderNotFoundError
from .gateway import NotificationGateway
from .models import DeliveryResult

_PERSISTED_DELIVERY_ERROR: Final = "provider reported a delivery failure"


def _is_finite_number(value: object) -> bool:
    if type(value) not in {int, float}:
        return False
    try:
        return isfinite(value)  # type: ignore[arg-type]
    except OverflowError:
        return False


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay_seconds: float = 5.0
    max_delay_seconds: float = 300.0
    lease_seconds: float = 60.0

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if not all(
            _is_finite_number(value)
            for value in (self.base_delay_seconds, self.max_delay_seconds, self.lease_seconds)
        ):
            raise ValueError("retry delays and lease must be finite numbers")
        if self.base_delay_seconds <= 0 or self.max_delay_seconds <= 0:
            raise ValueError("retry delays must be positive")
        if self.base_delay_seconds > self.max_delay_seconds:
            raise ValueError("base retry delay must not exceed maximum delay")
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

    def delay_for(self, attempt_no: int) -> float:
        if type(attempt_no) is not int or attempt_no < 1:
            raise ValueError("attempt_no must be at least one")
        exponent = attempt_no - 1
        if self.base_delay_seconds >= self.max_delay_seconds:
            return self.max_delay_seconds
        # The ratio between the smallest and largest finite IEEE-754 float is
        # below 2**2100. This guard avoids converting an attacker-sized int to
        # float while preserving every representable exponential delay.
        if exponent > 2_100:
            return self.max_delay_seconds
        try:
            delay = ldexp(self.base_delay_seconds, exponent)
        except OverflowError:
            return self.max_delay_seconds
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
            max_attempts=self.policy.max_attempts,
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
            retryable = error.retryable is True
            retry_at = (
                current + self.policy.delay_for(claim.attempt_no)
                if retryable and not exhausted
                else None
            )
            self.gateway.store.mark_failed(
                request.request_id,
                claim.attempt_no,
                retryable=retryable,
                exhausted=exhausted,
                error_code="provider_delivery_error",
                error_message=_PERSISTED_DELIVERY_ERROR,
                now=current,
                retry_at=retry_at,
            )
        except Exception:
            exhausted = claim.attempt_no >= self.policy.max_attempts
            retry_at = current + self.policy.delay_for(claim.attempt_no) if not exhausted else None
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
            if not isinstance(result, DeliveryResult) or result.provider != request.provider:
                self.gateway.store.mark_failed(
                    request.request_id,
                    claim.attempt_no,
                    retryable=False,
                    exhausted=True,
                    error_code="invalid_provider_result",
                    error_message="provider returned invalid delivery evidence",
                    now=current,
                    retry_at=None,
                )
            else:
                self.gateway.store.mark_delivered(
                    request.request_id,
                    claim.attempt_no,
                    result,
                    now=current,
                )
        return True
