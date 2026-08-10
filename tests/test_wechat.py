import json
from io import BytesIO
from typing import Any

import pytest

from notification_gateway import ConfigurationError, DeliveryError, Notification, WeChatProvider
from notification_gateway.providers import wechat

URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=secret"


class StubTransport:
    def __init__(self, status: int = 200, response: bytes = b'{"errcode":0,"errmsg":"ok"}'):
        self.status = status
        self.response = response
        self.call: tuple[str, bytes, float] | None = None

    def __call__(self, url: str, body: bytes, timeout: float) -> tuple[int, bytes]:
        self.call = (url, body, timeout)
        return self.status, self.response


@pytest.mark.parametrize("message_format", ["text", "markdown"])
def test_sends_supported_message(message_format: str) -> None:
    transport = StubTransport()
    provider = WeChatProvider(URL, timeout=3, transport=transport)
    result = provider.send(Notification("你好", format=message_format))

    assert result.provider == "wechat"
    assert result.details == {"errcode": 0, "errmsg": "ok"}
    assert transport.call is not None
    url, body, timeout = transport.call
    assert (url, timeout) == (URL, 3)
    assert json.loads(body) == {"msgtype": message_format, message_format: {"content": "你好"}}


@pytest.mark.parametrize(
    "url",
    [
        "http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x",
        "https://evil.example/cgi-bin/webhook/send?key=x",
        "https://qyapi.weixin.qq.com/wrong?key=x",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send",
    ],
)
def test_rejects_invalid_webhooks(url: str) -> None:
    with pytest.raises(ConfigurationError, match="webhook"):
        WeChatProvider(url)


def test_rejects_invalid_timeout_and_format() -> None:
    with pytest.raises(ConfigurationError, match="timeout"):
        WeChatProvider(URL, timeout=0)
    with pytest.raises(DeliveryError, match="does not support"):
        WeChatProvider(URL).send(Notification("hello", format="html"))


@pytest.mark.parametrize(("status", "retryable"), [(400, False), (429, True), (500, True)])
def test_http_errors(status: int, retryable: bool) -> None:
    with pytest.raises(DeliveryError, match=str(status)) as raised:
        WeChatProvider(URL, transport=StubTransport(status)).send(Notification("hello"))
    assert raised.value.retryable is retryable


@pytest.mark.parametrize("response", [b"not json", b"[]"])
def test_invalid_responses_are_retryable(response: bytes) -> None:
    with pytest.raises(DeliveryError) as raised:
        WeChatProvider(URL, transport=StubTransport(response=response)).send(Notification("hello"))
    assert raised.value.retryable


def test_wechat_api_error() -> None:
    response = b'{"errcode":93000,"errmsg":"invalid webhook"}'
    with pytest.raises(DeliveryError, match="93000.*invalid webhook") as raised:
        WeChatProvider(URL, transport=StubTransport(response=response)).send(Notification("hello"))
    assert not raised.value.retryable


def test_default_transport_success_and_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        status = 200

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        def read(self) -> bytes:
            return b"ok"

    monkeypatch.setattr(wechat, "urlopen", lambda request, timeout: Response())
    assert wechat._default_transport(URL, b"{}", 1) == (200, b"ok")

    def http_error(request: Any, timeout: float) -> None:
        raise wechat.HTTPError(URL, 503, "unavailable", {}, BytesIO(b"later"))

    monkeypatch.setattr(wechat, "urlopen", http_error)
    assert wechat._default_transport(URL, b"{}", 1) == (503, b"later")


def test_default_transport_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(request: Any, timeout: float) -> None:
        raise wechat.URLError("offline")

    monkeypatch.setattr(wechat, "urlopen", fail)
    with pytest.raises(DeliveryError, match="request failed") as raised:
        wechat._default_transport(URL, b"{}", 1)
    assert raised.value.retryable
