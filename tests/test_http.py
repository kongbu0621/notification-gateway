from __future__ import annotations

import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from conftest import make_request

from notification_gateway import (
    DeliveryResult,
    GatewayWSGIApp,
    NotificationGateway,
    SQLiteStore,
)
from notification_gateway.models import NotificationRequest


@dataclass
class Provider:
    name: str = "fake"

    def deliver(self, notification: NotificationRequest) -> DeliveryResult:
        return DeliveryResult(self.name)


def call(
    app: GatewayWSGIApp,
    method: str,
    path: str,
    *,
    payload: object | None = None,
    content_type: str = "application/json",
    token: str | None = None,
    content_length: str | None = None,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    raw = b"" if payload is None else json.dumps(payload).encode()
    environ: dict[str, Any] = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": str(len(raw)) if content_length is None else content_length,
        "wsgi.input": BytesIO(raw),
    }
    if token:
        environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    response = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], json.loads(response)


def app(tmp_path: Path, *, token: str | None = None) -> GatewayWSGIApp:
    service = NotificationGateway(SQLiteStore(tmp_path / "http.sqlite3"), [Provider()])
    return GatewayWSGIApp(service, auth_token=token)


def test_http_intake_replay_status_and_content_minimization(tmp_path: Path) -> None:
    service = app(tmp_path)
    payload = make_request().to_dict()
    status, headers, body = call(service, "POST", "/v1/notifications", payload=payload)
    assert status.startswith("202")
    assert headers["Location"] == "/v1/notifications/demo-event-001"
    assert headers["Cache-Control"] == "no-store"
    assert body["replayed"] is False

    status, _, body = call(service, "POST", "/v1/notifications", payload=payload)
    assert status.startswith("200")
    assert body["replayed"] is True

    status, _, body = call(service, "GET", "/v1/notifications/demo-event-001")
    assert status.startswith("200")
    serialized = json.dumps(body)
    assert "Example subject" not in serialized
    assert "Example notification" not in serialized
    assert "non-production" not in serialized
    assert "metadata" not in serialized


def test_http_conflict_unknown_provider_and_not_found(tmp_path: Path) -> None:
    service = app(tmp_path)
    payload = make_request().to_dict()
    assert call(service, "POST", "/v1/notifications", payload=payload)[0].startswith("202")
    changed = payload | {"title": "Changed"}
    assert call(service, "POST", "/v1/notifications", payload=changed)[0].startswith("409")
    unknown = make_request(request_id="other", provider="missing").to_dict()
    assert call(service, "POST", "/v1/notifications", payload=unknown)[0].startswith("422")
    assert call(service, "GET", "/v1/notifications/missing")[0].startswith("404")


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"unexpected": True},
    ],
)
def test_http_rejects_invalid_requests(tmp_path: Path, payload: object | None) -> None:
    assert call(app(tmp_path), "POST", "/v1/notifications", payload=payload)[0].startswith("400")


def test_http_content_type_length_routes_and_health(tmp_path: Path) -> None:
    service = app(tmp_path)
    assert call(service, "GET", "/healthz")[0].startswith("200")
    assert call(service, "GET", "/missing")[0].startswith("404")
    assert call(service, "POST", "/v1/notifications", payload={}, content_type="text/plain")[
        0
    ].startswith("415")
    assert call(service, "POST", "/v1/notifications", payload={}, content_length="bad")[
        0
    ].startswith("400")
    assert call(service, "POST", "/v1/notifications", payload={}, content_length="999999")[
        0
    ].startswith("413")
    assert call(service, "POST", "/v1/notifications", payload={}, content_length="3")[0].startswith(
        "400"
    )


def test_http_bearer_auth_is_secret_safe(tmp_path: Path) -> None:
    token = "t" * 32
    service = app(tmp_path, token=token)
    assert token not in repr(service)
    assert call(service, "GET", "/healthz")[0].startswith("200")
    assert call(service, "GET", "/v1/notifications/missing")[0].startswith("401")
    assert call(service, "GET", "/v1/notifications/missing", token="x" * 32)[0].startswith("401")
    assert call(service, "GET", "/v1/notifications/missing", token=token)[0].startswith("404")
    with pytest.raises(ValueError, match="32"):
        GatewayWSGIApp(service.gateway, auth_token="short")
