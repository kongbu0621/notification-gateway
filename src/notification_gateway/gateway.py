"""Durable intake and provider registration boundary."""

from __future__ import annotations

import time
from collections.abc import Iterable

from .exceptions import ConfigurationError, ProviderNotFoundError
from .models import NotificationRequest, RequestStatus
from .provider import NotificationProvider
from .store import EnqueueResult, SQLiteStore


class NotificationGateway:
    """Validate provider authority before durably accepting a notification."""

    def __init__(
        self,
        store: SQLiteStore,
        providers: Iterable[NotificationProvider] = (),
    ) -> None:
        self.store = store
        self._providers: dict[str, NotificationProvider] = {}
        for provider in providers:
            self.register(provider)

    @property
    def providers(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def register(self, provider: NotificationProvider, *, replace: bool = False) -> None:
        if not isinstance(provider, NotificationProvider):
            raise ConfigurationError("provider must implement NotificationProvider")
        name = provider.name.strip()
        if not name:
            raise ConfigurationError("provider name must not be empty")
        if name in self._providers and not replace:
            raise ConfigurationError(f"provider {name!r} is already registered")
        self._providers[name] = provider

    def provider(self, name: str) -> NotificationProvider:
        try:
            return self._providers[name]
        except KeyError as error:
            raise ProviderNotFoundError(f"provider {name!r} is not registered") from error

    def accept(
        self, notification: NotificationRequest, *, now: float | None = None
    ) -> EnqueueResult:
        self.provider(notification.provider)
        return self.store.enqueue(notification, now=time.time() if now is None else now)

    def status(self, request_id: str) -> RequestStatus:
        return self.store.get_status(request_id)
