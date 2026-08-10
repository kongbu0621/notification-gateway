"""WeCom (WeChat Work) group-robot webhook provider."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from ..exceptions import ConfigurationError, DeliveryError
from ..models import Notification, SendResult

Transport = Callable[[str, bytes, float], tuple[int, bytes]]


def _default_transport(url: str, body: bytes, timeout: float) -> tuple[int, bytes]:
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is validated
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()
    except (URLError, TimeoutError) as error:
        raise DeliveryError(f"WeChat request failed: {error}", retryable=True) from error


@dataclass(slots=True)
class WeChatProvider:
    """Send text or markdown to a WeCom group robot webhook."""

    webhook_url: str
    timeout: float = 10.0
    transport: Transport = _default_transport

    def __post_init__(self) -> None:
        parsed = urlparse(self.webhook_url)
        valid_host = parsed.hostname in {"qyapi.weixin.qq.com", "qyapi.weixin.qq.com.cn"}
        valid_path = parsed.path == "/cgi-bin/webhook/send"
        if parsed.scheme != "https" or not valid_host or not valid_path:
            raise ConfigurationError("invalid WeChat webhook URL")
        if not parse_qs(parsed.query).get("key"):
            raise ConfigurationError("WeChat webhook URL must contain a key")
        if self.timeout <= 0:
            raise ConfigurationError("timeout must be greater than zero")

    @property
    def name(self) -> str:
        return "wechat"

    def send(self, notification: Notification) -> SendResult:
        if notification.format not in {"text", "markdown"}:
            raise DeliveryError(f"WeChat does not support format {notification.format!r}")

        payload = {
            "msgtype": notification.format,
            notification.format: {"content": notification.content},
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        status, response_body = self.transport(self.webhook_url, body, self.timeout)
        if not 200 <= status < 300:
            raise DeliveryError(
                f"WeChat returned HTTP {status}", retryable=status == 429 or status >= 500
            )
        try:
            response: Any = json.loads(response_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DeliveryError(
                "WeChat returned an invalid JSON response", retryable=True
            ) from error
        if not isinstance(response, dict):
            raise DeliveryError("WeChat returned an invalid response", retryable=True)
        error_code = response.get("errcode")
        if error_code != 0:
            message = str(response.get("errmsg", "unknown error"))
            raise DeliveryError(f"WeChat rejected the message ({error_code}): {message}")
        return SendResult(
            provider=self.name,
            details={"errcode": 0, "errmsg": response.get("errmsg")},
        )
