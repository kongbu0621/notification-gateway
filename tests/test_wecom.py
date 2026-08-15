from __future__ import annotations

import json
import traceback
from io import BytesIO
from typing import Any

import pytest
from conftest import make_request

from notification_gateway import ConfigurationError, DeliveryError, WeComWebhookProvider
from notification_gateway.providers import wechat

URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-only-dummy-key"


class StubTransport:
    def __init__(self, status: int = 200, response: bytes = b'{"errcode":0,"errmsg":"ok"}'):
        self.status = status
        self.response = response
        self.call: tuple[str, bytes, float] | None = None

    def __call__(self, url: str, body: bytes, timeout: float) -> tuple[int, bytes]:
        self.call = (url, body, timeout)
        return self.status, self.response


def test_repr_and_delivery_do_not_expose_webhook_secret() -> None:
    transport = StubTransport()
    provider = WeComWebhookProvider(URL, timeout=3, transport=transport)
    assert "test-only-dummy-key" not in repr(provider)
    result = provider.deliver(make_request(provider="wecom"))
    assert result.provider == "wecom"
    assert result.details == {"accepted": True}
    assert transport.call is not None
    url, body, timeout = transport.call
    assert (url, timeout) == (URL, 3)
    payload = json.loads(body)
    assert payload["msgtype"] == "text"
    assert "Example notification" in payload["text"]["content"]


@pytest.mark.parametrize(
    "url",
    [
        "http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x",
        "https://example.invalid/cgi-bin/webhook/send?key=x",
        "https://qyapi.weixin.qq.com/wrong?key=x",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x#fragment",
        "https://@qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x",
        "https://qyapi.weixin.qq.com:8443/cgi-bin/webhook/send?key=x",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x&debug=true",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x&key=y",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key",
        "https://qyapi.weixin.qq.com.cn/cgi-bin/webhook/send?key=x",
    ],
)
def test_rejects_invalid_webhooks(url: str) -> None:
    with pytest.raises(ConfigurationError, match="webhook"):
        WeComWebhookProvider(url)


def test_rejects_invalid_timeout_and_oversized_content() -> None:
    with pytest.raises(ConfigurationError, match="timeout"):
        WeComWebhookProvider(URL, timeout=0)
    provider = WeComWebhookProvider(URL, transport=StubTransport())
    with pytest.raises(DeliveryError) as raised:
        provider.deliver(make_request(provider="wecom", body="界" * 2000))
    assert raised.value.retryable is False
    assert raised.value.code == "wecom_content_too_large"


@pytest.mark.parametrize(("status", "retryable"), [(400, False), (429, True), (500, True)])
def test_http_failures_are_normalized(status: int, retryable: bool) -> None:
    provider = WeComWebhookProvider(URL, transport=StubTransport(status=status))
    with pytest.raises(DeliveryError) as raised:
        provider.deliver(make_request(provider="wecom"))
    assert raised.value.retryable is retryable
    rendered = "".join(traceback.format_exception(raised.value))
    assert "test-only-dummy-key" not in rendered
    assert "errmsg" not in rendered


@pytest.mark.parametrize(
    "response",
    [b"not json", b"[]", b"{}", b'{"errcode":false}', b"x" * 65537],
)
def test_invalid_provider_responses_are_normalized(response: bytes) -> None:
    provider = WeComWebhookProvider(URL, transport=StubTransport(response=response))
    with pytest.raises(DeliveryError) as raised:
        provider.deliver(make_request(provider="wecom"))
    assert raised.value.retryable is True
    assert response.decode(errors="ignore") not in str(raised.value)


def test_api_error_does_not_echo_provider_message() -> None:
    response = b'{"errcode":93000,"errmsg":"secret-bearing provider text"}'
    provider = WeComWebhookProvider(URL, transport=StubTransport(response=response))
    with pytest.raises(DeliveryError) as raised:
        provider.deliver(make_request(provider="wecom"))
    assert raised.value.code == "wecom_api_93000"
    assert "secret-bearing" not in str(raised.value)


def test_default_transport_success_http_and_network_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        def read(self, limit: int) -> bytes:
            assert limit == 65537
            return b"ok"

    monkeypatch.setattr(wechat, "urlopen", lambda request, timeout: Response())
    assert wechat._default_transport(URL, b"{}", 1) == (200, b"ok")

    def http_error(request: Any, timeout: float) -> None:
        raise wechat.HTTPError(URL, 503, "unavailable", {}, BytesIO(b"later"))

    monkeypatch.setattr(wechat, "urlopen", http_error)
    assert wechat._default_transport(URL, b"{}", 1) == (503, b"later")

    def network_error(request: Any, timeout: float) -> None:
        raise wechat.URLError("offline")

    monkeypatch.setattr(wechat, "urlopen", network_error)
    with pytest.raises(DeliveryError) as raised:
        wechat._default_transport(URL, b"{}", 1)
    assert raised.value.__cause__ is None
    assert "test-only-dummy-key" not in "".join(traceback.format_exception(raised.value))
