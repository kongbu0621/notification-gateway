# notification-gateway

[English](README.md) | **简体中文**

`notification-gateway` 是一个小型、带类型标注（typed）的 Python service/library，用于持久化接收通知请求，并通过可插拔 Provider 完成投递。v0.1 使用 SQLite、at-least-once Worker、最小化 WSGI HTTP boundary，以及兼容 WeCom（企业微信）群机器人的 Adapter。

本工程负责 request validation、durable intake、provider routing、bounded retry、restart recovery、status、retention cleanup，以及 secret-safe delivery outcome accounting。它不负责调用方特定的 monitoring、scheduling、scraping、product workflow、recipient management 或 business rules。

## 项目状态（Status）

v0.1 是 alpha software，仅适用于 loopback 或受控 private network 部署。它不是经过加固的公网入口（hardened public Internet edge），不提供 public-service authentication、tenant isolation、TLS termination 或通用 abuse protection。

## 开发安装（Install for development）

本工程目前尚未作为正式 PyPI release 发布。请从检出的源码目录安装：

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

要求 Python 3.11 或更高版本。

## 稳定请求合同（Stable request contract）

Python model 与 `schemas/notification-request-v1.json` 对 v1 字段、canonical UTC timestamp 语法以及 metadata 的 shape/depth 规则采用一致的严格定义。Python boundary 还会执行 JSON Schema 无法跨实现可靠表达的 UTF-8 byte limits。

```json
{
  "schema_version": "1",
  "request_id": "demo-event-001",
  "idempotency_key": "demo-object:available",
  "provider": "wecom",
  "subject": "Example subject",
  "title": "Example notification",
  "body": "A non-production example event is available.",
  "severity": "info",
  "metadata": {"event_ref": "example-001"},
  "created_at": "2026-01-01T00:00:00.000000Z"
}
```

`request_id` 标识一次持久通知事件。完全相同的请求重复提交具有 idempotent 语义；使用相同 `request_id` 提交不同内容会产生 conflict。`idempotency_key` 是调用方的 correlation context，不是全局唯一键；后续合法事件可以使用新的 request ID 并复用该值。

`NotificationRequest` model 会拒绝缺失或多余字段、非 UTC timestamp、非法 identifier、不支持的 severity、过深嵌套、非 JSON metadata 以及超限内容。

## Python API

```python
import os
from datetime import UTC, datetime

from notification_gateway import (
    DeliveryWorker,
    NotificationGateway,
    NotificationRequest,
    SQLiteStore,
    WeComWebhookProvider,
)

store = SQLiteStore("runtime-data/notifications.sqlite3")
provider = WeComWebhookProvider(os.environ["WECOM_WEBHOOK_URL"])
gateway = NotificationGateway(store, [provider])

request = NotificationRequest(
    request_id="demo-event-001",
    idempotency_key="demo-object:available",
    provider="wecom",
    subject="Example subject",
    title="Example notification",
    body="A non-production example event is available.",
    severity="info",
    metadata={"event_ref": "example-001"},
    created_at=datetime.now(UTC),
)

accepted = gateway.accept(request)
DeliveryWorker(gateway).run_once()
print(gateway.status(accepted.status.request_id).state)
```

intake transaction 会先于 Provider I/O 完成 commit。Worker 使用短期 lease 认领任务，释放 SQLite write transaction 后调用 Provider，最后再持久化 outcome。如果 Provider 已接受通知、但进程在本地 acknowledgement 前崩溃，相同 `request_id` 可能再次投递；本工程明确不声称 exactly-once delivery。

## 本地 HTTP 边界（Local HTTP boundary）

通过 runtime configuration 设置配置，不要提交 `.env`：

```bash
export WECOM_WEBHOOK_URL='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=runtime-secret'
notification-gateway --db runtime-data/notifications.sqlite3 serve
```

Server 默认绑定 `127.0.0.1:8787`，提供以下 endpoint：

- `POST /v1/notifications` — 校验并持久 enqueue；
- `GET /v1/notifications/{request_id}` — 返回不含通知内容的 status；
- `GET /healthz` — 本地 health check。

在 private supervisor 或 scheduler 中运行 `notification-gateway ... work-once`，每次投递一个到期请求。运行 `notification-gateway ... purge`，按配置的 retention period 删除 terminal records。

除非显式指定 `--allow-non-loopback`，否则拒绝 non-loopback 绑定；同时还必须配置至少 32 个字符的 `NOTIFICATION_GATEWAY_AUTH_TOKEN`。该 token 只是最小 service boundary。经过审查的部署还必须提供 TLS termination、network access control、rate limiting、monitoring 与 incident response。

`request_id` 是 identifier，不是 authorization credential。

## 重试与重启行为（Retry and restart behavior）

- Delivery 采用 at-least-once 语义。
- retry 使用 bounded exponential backoff，并显式限制最大 attempts 与 delay；crash lease 过期后恢复出的 attempt 也计入上限。
- retry state 与 attempt evidence 在重启后仍然保留。
- 当 attempt budget 尚未耗尽时，过期的 in-flight lease 会使用同一个 request ID 恢复为 retryable work；否则进入 `dead`。
- permanent failure 或 attempts exhausted 会进入 `dead` 状态。
- Provider I/O 不会在 SQLite write transaction 内执行。
- Provider error text 与 provider-controlled error code 永不持久化；只保存 gateway-owned code 与 generic message。
- Provider 返回的 message ID 与 details 仅存在于进程内，不写入 SQLite、log、status response 或 attempt evidence。

## 密钥与隐私（Secrets and privacy）

Webhook URL 与 authentication token 只能来自 runtime configuration。Secret fields 不进入 object representation。Gateway/Provider 所有的 URL、query string、header、identifier、details、raw response、error code 和 exception text 都不得进入 log、HTTP error、SQLite、attempt、fixture 或 audit evidence。

通知内容与 metadata 是 opaque caller data。它们会被持久化并转发，而 Gateway 无法可靠判断其中是否包含 password、token、URL、个人信息（personal information）或其他敏感材料。Gateway 不判断这些处理是否合法。调用方和运营者仍负责 purpose limitation、data minimization、lawful basis、notice/consent、敏感个人信息与未成年人信息、retention、deletion、individual rights、Provider contract 以及 cross-border transfer rules。

应优先发送 generic content 与 opaque event reference。绝不能在通知请求中放入 password、token、recovery code、身份证件号码、financial credential、无限制的学生档案或其他非必要个人信息。

参见[隐私与中国大陆部署边界](docs/privacy-and-mainland-china.zh-CN.md)和[安全策略](SECURITY.zh-CN.md)。对应英文文档为 [privacy and mainland-China deployment boundary](docs/privacy-and-mainland-china.md) 与 [security policy](SECURITY.md)。

## 数据保留（Retention）

SQLite durability 不是永久消息归档。`purge_terminal` 会在显式 retention period 到期后删除 delivered/dead request 及其 attempts，同时保留 pending/retryable authority。运营者必须另外管理 SQLite WAL、backup、filesystem permission、适用时的 encrypted storage，以及 incident-driven deletion requirements。

## 新增 Provider（Add a provider）

Provider 必须实现稳定的 `name` 与 `deliver(NotificationRequest) -> DeliveryResult`。Provider transport 必须可注入，保证测试不访问 external service。`DeliveryError` 始终只呈现 generic text；其 code 与 retryability 仅是进程内 control。`DeliveryResult` 的 message ID 与 details 是 opaque evidence，不进入 representation，Worker 不对其进行 validation、serialization、logging 或 persistence；只有与请求匹配的 Provider name 决定 success。Worker 只持久化 gateway-owned outcome、retryability 与 generic failure classification。

每个 Provider 必须记录 operator、已知情况下的 expected data region、可能的 cross-border transfer、允许的数据分类、size limit、retry semantics 与 approval requirements。不能只根据 hostname 就声称数据留在某个 jurisdiction。

## 验证（Verification）

```bash
pytest
ruff format --check .
ruff check .
mypy
python -m pip check
python -m build
git diff --check
```

GitHub Actions 会在 Python 3.11–3.13 上运行完整 test suite。测试不得访问 external network。

## 许可证与商标（License and trademarks）

本工程采用 Apache-2.0 License。

WeCom、WeChat 及相关名称是其各自权利人的 trademark。本独立工程与 Tencent（腾讯）不存在隶属、背书或赞助关系；Provider 名称仅用于描述与用户自行配置 endpoint 的 interoperability。
