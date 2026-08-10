"""Provider contract."""

from typing import Protocol, runtime_checkable

from .models import Notification, SendResult


@runtime_checkable
class NotificationProvider(Protocol):
    """Structural interface implemented by all delivery providers."""

    @property
    def name(self) -> str:
        """Return the stable provider name used by the registry."""
        ...

    def send(self, notification: Notification) -> SendResult:
        """Deliver a notification or raise ``DeliveryError``."""
        ...
