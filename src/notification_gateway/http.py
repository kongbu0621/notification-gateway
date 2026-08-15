"""Minimal WSGI boundary for durable intake and content-free status lookup."""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Callable
from http import HTTPStatus
from typing import Any, Final, cast
from urllib.parse import unquote

from .exceptions import (
    ConflictError,
    ProviderNotFoundError,
    RequestNotFoundError,
    ValidationError,
)
from .gateway import NotificationGateway
from .models import MAX_REQUEST_BYTES, NotificationRequest

StartResponse = Callable[[str, list[tuple[str, str]]], Any]
_STATUS_PATH: Final = re.compile(r"^/v1/notifications/([A-Za-z0-9][A-Za-z0-9._:-]{0,127})$")
MAX_HTTP_BODY_BYTES: Final = MAX_REQUEST_BYTES + 1_024


def _response(
    start_response: StartResponse,
    status: HTTPStatus,
    payload: dict[str, object],
    *,
    extra_headers: list[tuple[str, str]] | None = None,
) -> list[bytes]:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    start_response(f"{status.value} {status.phrase}", headers)
    return [body]


class GatewayWSGIApp:
    """WSGI application; an optional bearer token protects non-health routes."""

    def __init__(self, gateway: NotificationGateway, *, auth_token: str | None = None) -> None:
        if auth_token is not None and len(auth_token) < 32:
            raise ValueError("auth_token must be at least 32 characters")
        self.gateway = gateway
        self._auth_token = auth_token

    def __repr__(self) -> str:
        return f"GatewayWSGIApp(gateway={self.gateway!r}, auth_token=<redacted>)"

    def _authorized(self, environ: dict[str, Any]) -> bool:
        if self._auth_token is None:
            return True
        supplied = cast(str, environ.get("HTTP_AUTHORIZATION", ""))
        prefix = "Bearer "
        return supplied.startswith(prefix) and hmac.compare_digest(
            supplied[len(prefix) :], self._auth_token
        )

    def __call__(self, environ: dict[str, Any], start_response: StartResponse) -> list[bytes]:
        method = cast(str, environ.get("REQUEST_METHOD", "GET")).upper()
        path = unquote(cast(str, environ.get("PATH_INFO", "/")))
        if method == "GET" and path == "/healthz":
            return _response(start_response, HTTPStatus.OK, {"status": "ok"})
        if not self._authorized(environ):
            return _response(
                start_response,
                HTTPStatus.UNAUTHORIZED,
                {"error": "unauthorized"},
                extra_headers=[("WWW-Authenticate", "Bearer")],
            )
        if method == "POST" and path == "/v1/notifications":
            return self._post(environ, start_response)
        match = _STATUS_PATH.fullmatch(path)
        if method == "GET" and match:
            return self._get_status(match.group(1), start_response)
        return _response(start_response, HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def _post(self, environ: dict[str, Any], start_response: StartResponse) -> list[bytes]:
        content_type = cast(str, environ.get("CONTENT_TYPE", ""))
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            return _response(
                start_response,
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": "content_type_must_be_application_json"},
            )
        length_text = cast(str, environ.get("CONTENT_LENGTH", ""))
        try:
            length = int(length_text) if length_text else MAX_HTTP_BODY_BYTES + 1
        except ValueError:
            return _response(start_response, HTTPStatus.BAD_REQUEST, {"error": "invalid_length"})
        if length < 0 or length > MAX_HTTP_BODY_BYTES:
            return _response(
                start_response, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request_too_large"}
            )
        stream = environ.get("wsgi.input")
        if stream is None or not hasattr(stream, "read"):
            return _response(start_response, HTTPStatus.BAD_REQUEST, {"error": "missing_body"})
        raw = cast(Any, stream).read(length)
        if not isinstance(raw, bytes) or not raw or len(raw) != length:
            return _response(start_response, HTTPStatus.BAD_REQUEST, {"error": "missing_body"})
        try:
            decoded = raw.decode("utf-8")
            notification = NotificationRequest.from_json(decoded)
            result = self.gateway.accept(notification)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            message = (
                str(error) if isinstance(error, ValidationError) else "request is not valid JSON"
            )
            return _response(
                start_response,
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_request", "message": message[:160]},
            )
        except ProviderNotFoundError:
            return _response(
                start_response,
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"error": "unknown_provider"},
            )
        except ConflictError:
            return _response(
                start_response,
                HTTPStatus.CONFLICT,
                {"error": "request_id_conflict"},
            )
        status = HTTPStatus.OK if result.replayed else HTTPStatus.ACCEPTED
        return _response(
            start_response,
            status,
            {"replayed": result.replayed, "notification": result.status.to_dict()},
            extra_headers=[("Location", f"/v1/notifications/{notification.request_id}")],
        )

    def _get_status(self, request_id: str, start_response: StartResponse) -> list[bytes]:
        try:
            status = self.gateway.status(request_id)
        except RequestNotFoundError:
            return _response(start_response, HTTPStatus.NOT_FOUND, {"error": "not_found"})
        return _response(start_response, HTTPStatus.OK, {"notification": status.to_dict()})
