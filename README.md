# notification-gateway

A typed, reusable Python notification gateway with pluggable delivery providers.
Version 0.1 ships with a WeCom (WeChat Work) group-robot provider and keeps the
gateway core independent of HTTP frameworks and third-party runtime dependencies.

## Install

```bash
python -m pip install notification-gateway
```

Python 3.10 or newer is required.

## Quick start

```python
import os

from notification_gateway import Notification, NotificationGateway, WeChatProvider

wechat = WeChatProvider(os.environ["WECHAT_WEBHOOK_URL"])
gateway = NotificationGateway([wechat])

result = gateway.send("wechat", Notification("Deployment complete"))
print(result.provider)
```

The webhook URL must be an HTTPS WeCom robot endpoint of the form
`https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...`. Treat it as a secret:
load it from a secret manager or environment variable and never commit it.

Markdown is supported by selecting the portable message format:

```python
gateway.send("wechat", Notification("**Build passed**", format="markdown"))
```

## Add a provider

Providers use structural typing. Implement a `name` property and synchronous
`send` method; inheriting from package classes is not necessary:

```python
from notification_gateway import Notification, SendResult

class ConsoleProvider:
    @property
    def name(self) -> str:
        return "console"

    def send(self, notification: Notification) -> SendResult:
        print(notification.content)
        return SendResult(provider=self.name)
```

Register instances through `NotificationGateway.register`. Duplicate names are
rejected unless `replace=True` is supplied explicitly. Provider failures use
`DeliveryError`; its `retryable` attribute lets callers decide whether their own
queue or job runner should retry. The library itself does not retry, avoiding
surprise duplicate notifications.

## Development

```bash
python -m pip install -e '.[dev]'
pytest
ruff check .
mypy
python -m build
```

The CI matrix runs tests, linting, and strict type checking on every supported
Python version.
