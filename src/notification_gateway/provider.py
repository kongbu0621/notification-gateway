"""Typed delivery-provider contract."""

from typing import Protocol, runtime_checkable

from .models import DeliveryResult, NotificationRequest


@runtime_checkable
class NotificationProvider(Protocol):
    """Structural interface implemented by delivery providers."""

    @property
    def name(self) -> str:
        """Return the stable provider identity used for routing."""
        ...

    def deliver(self, notification: NotificationRequest) -> DeliveryResult:
        """Deliver one request or raise a secret-safe DeliveryError."""
        ...
