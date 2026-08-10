from dataclasses import dataclass

import pytest

from notification_gateway import (
    ConfigurationError,
    Notification,
    NotificationGateway,
    ProviderNotFoundError,
    SendResult,
)


@dataclass
class FakeProvider:
    name: str = "fake"
    received: Notification | None = None

    def send(self, notification: Notification) -> SendResult:
        self.received = notification
        return SendResult(self.name, "42", {"accepted": True})


def test_register_send_unregister() -> None:
    provider = FakeProvider()
    gateway = NotificationGateway([provider])
    notification = Notification("hello", metadata={"trace": "one"})

    result = gateway.send("fake", notification)

    assert gateway.providers == ("fake",)
    assert provider.received == notification
    assert result == SendResult("fake", "42", {"accepted": True})
    assert gateway.unregister("fake") is provider


def test_registry_errors_and_explicit_replacement() -> None:
    first = FakeProvider()
    replacement = FakeProvider()
    gateway = NotificationGateway([first])
    with pytest.raises(ConfigurationError, match="already registered"):
        gateway.register(replacement)
    gateway.register(replacement, replace=True)
    assert gateway.unregister("fake") is replacement
    with pytest.raises(ProviderNotFoundError, match="not registered"):
        gateway.unregister("fake")
    with pytest.raises(ProviderNotFoundError, match="not registered"):
        gateway.send("missing", Notification("hello"))


@pytest.mark.parametrize("provider", [object(), FakeProvider(name=" ")])
def test_invalid_provider(provider: object) -> None:
    with pytest.raises(ConfigurationError):
        NotificationGateway().register(provider)  # type: ignore[arg-type]


def test_models_validate_and_copy_input_mappings() -> None:
    metadata = {"mutable": True}
    notification = Notification("hello", metadata=metadata)
    metadata["later"] = True
    assert dict(notification.metadata) == {"mutable": True}
    with pytest.raises(TypeError):
        notification.metadata["no"] = True  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="content"):
        Notification("  ")
    with pytest.raises(ConfigurationError, match="format"):
        Notification("hello", format=" ")
