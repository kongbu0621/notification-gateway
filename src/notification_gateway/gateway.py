"""Durable intake and provider registration boundary."""

from __future__ import annotations

import time
from collections.abc import Iterable
from contextlib import suppress

from .exceptions import ConfigurationError, ProviderNotFoundError
from .models import NotificationRequest, RequestStatus, _is_provider_name
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
        name: object = None
        deliver: object = None
        with suppress(Exception):
            name = provider.name
            deliver = provider.deliver
        if not _is_provider_name(name) or not callable(deliver):
            raise ConfigurationError("provider must implement a valid NotificationProvider")
        if name in self._providers and not replace:
            raise ConfigurationError("provider is already registered")
        self._providers[name] = provider

    def provider(self, name: str) -> NotificationProvider:
        if not _is_provider_name(name):
            raise ProviderNotFoundError("provider is not registered")
        provider = self._providers.get(name)
        if provider is None:
            raise ProviderNotFoundError("provider is not registered")
        return provider

    def accept(
        self, notification: NotificationRequest, *, now: float | None = None
    ) -> EnqueueResult:
        self.provider(notification.provider)
        return self.store.enqueue(notification, now=time.time() if now is None else now)

    def status(self, request_id: str) -> RequestStatus:
        return self.store.get_status(request_id)
