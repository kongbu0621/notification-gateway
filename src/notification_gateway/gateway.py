"""Provider registration and notification routing."""

from collections.abc import Iterable

from .exceptions import ConfigurationError, ProviderNotFoundError
from .models import Notification, SendResult
from .provider import NotificationProvider


class NotificationGateway:
    """Route notifications through registered providers."""

    def __init__(self, providers: Iterable[NotificationProvider] = ()) -> None:
        self._providers: dict[str, NotificationProvider] = {}
        for provider in providers:
            self.register(provider)

    @property
    def providers(self) -> tuple[str, ...]:
        """Return registered provider names in registration order."""
        return tuple(self._providers)

    def register(self, provider: NotificationProvider, *, replace: bool = False) -> None:
        """Register a provider.

        Duplicate names are rejected by default to make accidental configuration
        changes visible. Set ``replace`` explicitly for intentional replacement.
        """
        if not isinstance(provider, NotificationProvider):
            raise ConfigurationError("provider must implement NotificationProvider")
        name = provider.name.strip()
        if not name:
            raise ConfigurationError("provider name must not be empty")
        if name in self._providers and not replace:
            raise ConfigurationError(f"provider {name!r} is already registered")
        self._providers[name] = provider

    def unregister(self, name: str) -> NotificationProvider:
        """Remove and return a provider."""
        try:
            return self._providers.pop(name)
        except KeyError as error:
            raise ProviderNotFoundError(f"provider {name!r} is not registered") from error

    def send(self, provider: str, notification: Notification) -> SendResult:
        """Deliver ``notification`` using the named provider."""
        try:
            selected = self._providers[provider]
        except KeyError as error:
            raise ProviderNotFoundError(f"provider {provider!r} is not registered") from error
        return selected.send(notification)
