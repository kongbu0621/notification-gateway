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
    """A provider failed without exposing provider secrets or raw responses."""

    def __init__(
        self,
        message: str = "notification delivery failed",
        *,
        retryable: bool = False,
        code: str = "delivery_error",
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.code = code
