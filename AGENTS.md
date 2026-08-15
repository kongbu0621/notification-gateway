# Repository invariants

**English** | [简体中文](AGENTS.zh-CN.md)

These rules are part of the v0.1 contract and apply to humans and coding agents.

1. Durable acceptance precedes provider I/O. Accepted work must survive provider failure and restart.
2. `request_id` identifies one occurrence. Reusing it with a different payload is a conflict. `idempotency_key` is correlation context and is not globally unique.
3. Delivery is at-least-once. Never claim exactly-once behavior.
4. Provider network I/O must never run inside a SQLite write transaction.
5. Pending or retryable work may not be silently deleted, downgraded, or acknowledged as delivered.
6. Runtime gateway- or provider-owned webhook URLs, tokens, headers, raw responses, identifiers, details, and secret-bearing exception chains must never enter repr output, logs, HTTP responses, SQLite, fixtures, audit evidence, or Git history. Tests may use unmistakably dummy values under invariant 12. Caller-supplied notification content is opaque, is durably persisted, and is governed by invariant 14.
7. Persisted errors are normalized, bounded, and secret-safe. Unexpected provider exceptions use a generic error.
8. Status projections exclude notification subject, title, body, metadata, and provider credentials.
9. Runtime databases, WALs, logs, backups, and `.env` files are never committed.
10. Terminal records have explicit retention and a tested purge path. Durability is not indefinite retention.
11. The core stays domain-agnostic. Caller-specific monitoring, scheduling, scraping, workflow, or business rules do not belong here.
12. Examples and tests use unmistakable dummy identities, content, endpoints, and credentials and never use external network access.
13. HTTP defaults to loopback/private operation. Non-loopback operation requires explicit opt-in, authentication, TLS termination, network controls, rate limiting, and a security review.
14. The gateway does not decide whether caller-supplied personal information is lawful. Documentation must preserve the caller/operator responsibility boundary.
15. New providers document operator identity, expected data region when known, possible cross-border transfer, supported data classification, and approval requirements. Do not assert data residency without evidence.
16. Executor claims are not closure evidence. Tests, repository state, CI, and independent diff/history review are required.
