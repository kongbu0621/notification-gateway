"""Provider-neutral request and response models."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .exceptions import ConfigurationError


@dataclass(frozen=True, slots=True)
class Notification:
    """Content to deliver.

    ``format`` is deliberately a string so new provider capabilities can be added
    without changing the core. v0.1 providers support ``text`` and ``markdown``.
    """

    content: str
    format: str = "text"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.content or not self.content.strip():
            raise ConfigurationError("notification content must not be empty")
        if not self.format or not self.format.strip():
            raise ConfigurationError("notification format must not be empty")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class SendResult:
    """Successful delivery information returned by a provider."""

    provider: str
    message_id: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))
