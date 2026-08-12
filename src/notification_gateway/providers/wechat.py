"""Secret-safe WeCom group-robot webhook provider."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlparse
from urllib.request import Request, urlopen

from ..exceptions import ConfigurationError, DeliveryError
from ..models import DeliveryResult, NotificationRequest

Transport = Callable[[str, bytes, float], tuple[int, bytes]]
_ALLOWED_HOSTS: Final = frozenset({"qyapi.weixin.qq.com"})
_WEBHOOK_PATH: Final = "/cgi-bin/webhook/send"
_MAX_PROVIDER_CONTENT_BYTES: Final = 4_096
_MAX_RESPONSE_BYTES: Final = 65_536


def _default_transport(url: str, body: bytes, timeout: float) -> tuple[int, bytes]:
    # The provider validates HTTPS, host, path, fragment, and key before this transport runs.
    request = Request(  # noqa: S310
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        return error.code, error.read(_MAX_RESPONSE_BYTES + 1)
    except (URLError, TimeoutError, OSError):
        raise DeliveryError(
            "WeCom transport failed",
            retryable=True,
            code="wecom_transport_error",
        ) from None


@dataclass(slots=True)
class WeComWebhookProvider:
    """Deliver text messages without representing or persisting the webhook secret."""

    webhook_url: str = field(repr=False)
    timeout: float = 10.0
    transport: Transport = field(default=_default_transport, repr=False)

    def __post_init__(self) -> None:
        parsed = urlparse(self.webhook_url)
        try:
            query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
            port = parsed.port
        except ValueError:
            raise ConfigurationError("invalid WeCom webhook URL") from None
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _ALLOWED_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or parsed.path != _WEBHOOK_PATH
            or parsed.fragment
            or len(query) != 1
            or query[0][0] != "key"
            or not query[0][1].strip()
        ):
            raise ConfigurationError("invalid WeCom webhook URL")
        if self.timeout <= 0:
            raise ConfigurationError("timeout must be greater than zero")

    @property
    def name(self) -> str:
        return "wecom"

    def deliver(self, notification: NotificationRequest) -> DeliveryResult:
        heading = f"[{notification.severity.upper()}] {notification.title}"
        content = "\n".join(
            part for part in (heading, notification.subject, notification.body) if part
        )
        if len(content.encode("utf-8")) > _MAX_PROVIDER_CONTENT_BYTES:
            raise DeliveryError(
                "notification exceeds the WeCom provider size limit",
                retryable=False,
                code="wecom_content_too_large",
            )
        payload = {"msgtype": "text", "text": {"content": content}}
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        status, response_body = self.transport(self.webhook_url, body, self.timeout)
        if len(response_body) > _MAX_RESPONSE_BYTES:
            raise DeliveryError(
                "WeCom returned an oversized response",
                retryable=True,
                code="wecom_response_too_large",
            )
        if not 200 <= status < 300:
            retryable = status == 429 or status >= 500
            raise DeliveryError(
                "WeCom returned an unsuccessful HTTP status",
                retryable=retryable,
                code=f"wecom_http_{status}" if 100 <= status <= 599 else "wecom_http_error",
            )
        try:
            response: Any = json.loads(response_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DeliveryError(
                "WeCom returned an invalid response",
                retryable=True,
                code="wecom_invalid_response",
            ) from None
        if not isinstance(response, dict) or not isinstance(response.get("errcode"), int):
            raise DeliveryError(
                "WeCom returned an invalid response",
                retryable=True,
                code="wecom_invalid_response",
            )
        error_code = response["errcode"]
        if error_code != 0:
            raise DeliveryError(
                "WeCom rejected the notification",
                retryable=False,
                code=f"wecom_api_{error_code}"
                if 0 < error_code < 1_000_000_000
                else "wecom_api_error",
            )
        return DeliveryResult(provider=self.name, details={"accepted": True})


# Compatibility alias for the pre-v0.1 prototype. It intentionally keeps the
# safer WeCom name in documentation and object representations.
WeChatProvider = WeComWebhookProvider
