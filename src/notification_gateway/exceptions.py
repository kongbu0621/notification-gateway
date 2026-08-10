"""Exceptions raised by the gateway."""


class NotificationGatewayError(Exception):
    """Base exception for this package."""


class ConfigurationError(NotificationGatewayError, ValueError):
    """A provider or gateway is configured incorrectly."""


class ProviderNotFoundError(NotificationGatewayError, LookupError):
    """The requested provider is not registered."""


class DeliveryError(NotificationGatewayError):
    """A provider could not deliver a notification."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
