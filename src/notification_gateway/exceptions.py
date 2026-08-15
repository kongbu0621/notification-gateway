"""Public exception hierarchy with secret-safe messages."""

from __future__ import annotations


class NotificationGatewayError(Exception):
    """Base exception for notification-gateway."""


class ConfigurationError(NotificationGatewayError, ValueError):
    """The gateway or a provider is configured incorrectly."""


class ValidationError(NotificationGatewayError, ValueError):
    """A notification request does not satisfy the v1 contract."""


class ConflictError(NotificationGatewayError):
    """A request identifier was reused with a different payload."""


class RequestNotFoundError(NotificationGatewayError, LookupError):
    """A durable notification request does not exist."""


class ProviderNotFoundError(NotificationGatewayError, LookupError):
    """The requested provider is not registered."""


class DeliveryError(NotificationGatewayError):
    """A provider failed; rendered text is deliberately fixed and secret-safe."""

    def __init__(
        self,
        message: str = "notification delivery failed",
        *,
        retryable: bool = False,
        code: str = "delivery_error",
    ) -> None:
        # Provider-authored exception text is not a safe logging boundary. Keep
        # the parameter for v0.1 source compatibility, but never retain or
        # render it; callers can branch on the in-process code and retryability.
        del message
        super().__init__("notification delivery failed")
        self.retryable = retryable
        self.code = code
