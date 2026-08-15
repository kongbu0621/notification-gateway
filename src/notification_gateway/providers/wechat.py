"""Secret-safe WeCom group-robot webhook provider."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from threading import TIMEOUT_MAX
from typing import Any, Final
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlparse
from urllib.request import Request, urlopen

from ..exceptions import ConfigurationError, DeliveryError
from ..models import DeliveryResult, NotificationRequest

Transport = Callable[[str, bytes, float], tuple[int, bytes]]
_ALLOWED_HOSTS: Final = frozenset({"qyapi.weixin.qq.com"})
_WEBHOOK_PATH: Final = "/cgi-bin/webhook/send"
_MAX_PROVIDER_CONTENT_BYTES: Final = 4_096
_MAX_RESPONSE_BYTES: Final = 65_536
_TRANSPORT_FAILED: Final = object()
_INVALID_RESPONSE: Final = object()


def _default_transport(url: str, body: bytes, timeout: float) -> tuple[int, bytes]:
    # The provider validates HTTPS, host, path, fragment, and key before this transport runs.
    request = Request(  # noqa: S310
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    result: tuple[int, bytes] | None = None
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            result = response.status, response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        with suppress(Exception):
            result = error.code, error.read(_MAX_RESPONSE_BYTES + 1)
    except Exception:
        result = None
    if result is None:
        raise DeliveryError(
            "WeCom transport failed",
            retryable=True,
            code="wecom_transport_error",
        )
    return result


@dataclass(slots=True)
class WeComWebhookProvider:
    """Deliver text messages without representing or persisting the webhook secret."""

    webhook_url: str = field(repr=False)
    timeout: float = 10.0
    transport: Transport = field(default=_default_transport, repr=False)

    def __post_init__(self) -> None:
        parsed = None
        query = None
        port = None
        if type(self.webhook_url) is not str:
            raise ConfigurationError("invalid WeCom webhook URL")
        try:
            parsed = urlparse(self.webhook_url)
            query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
            port = parsed.port
        except (TypeError, ValueError):
            parsed = None
            query = None
        if (
            parsed is None
            or query is None
            or parsed.scheme != "https"
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
        if type(self.timeout) not in {int, float}:
            raise ConfigurationError("timeout must be a finite supported number")
        timeout = None
        with suppress(OverflowError):
            timeout = float(self.timeout)
        if timeout is None or not math.isfinite(timeout) or not 0 < timeout <= TIMEOUT_MAX:
            raise ConfigurationError("timeout must be a finite supported number")
        self.timeout = timeout

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
        transport_result: object = _TRANSPORT_FAILED
        with suppress(Exception):
            transport_result = self.transport(self.webhook_url, body, self.timeout)
        if transport_result is _TRANSPORT_FAILED:
            raise DeliveryError(
                "WeCom transport failed",
                retryable=True,
                code="wecom_transport_error",
            )
        if type(transport_result) is not tuple or len(transport_result) != 2:
            raise DeliveryError(
                "WeCom returned an invalid response",
                retryable=True,
                code="wecom_invalid_response",
            )
        status, response_body = transport_result
        if type(status) is not int or not 100 <= status <= 599 or type(response_body) is not bytes:
            raise DeliveryError(
                "WeCom returned an invalid response",
                retryable=True,
                code="wecom_invalid_response",
            )
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
        response: Any = _INVALID_RESPONSE
        with suppress(UnicodeDecodeError, ValueError, RecursionError):
            response = json.loads(response_body)
        if response is _INVALID_RESPONSE:
            raise DeliveryError(
                "WeCom returned an invalid response",
                retryable=True,
                code="wecom_invalid_response",
            )
        if not isinstance(response, dict) or type(response.get("errcode")) is not int:
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
