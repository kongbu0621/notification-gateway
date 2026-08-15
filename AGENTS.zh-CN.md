# 仓库不变量（Repository invariants）

[English](AGENTS.md) | **简体中文**

以下规则属于 v0.1 contract，适用于人类贡献者与 coding agent。

1. durable acceptance 必须先于 Provider I/O。已经接受的任务必须能够承受 Provider failure 与进程 restart。
2. `request_id` 标识一次通知事件。使用相同 `request_id` 提交不同 payload 会产生 conflict。`idempotency_key` 是 correlation context，不是全局唯一键。
3. Delivery 采用 at-least-once 语义，绝不能声称 exactly-once behavior。
4. Provider network I/O 绝不能在 SQLite write transaction 内执行。
5. pending 或 retryable work 不得被静默删除、降级或确认成 delivered。
6. 运行时由 Gateway 或 Provider 控制的 webhook URL、token、header、raw response、identifier、details 与包含 secret 的 exception chain，绝不能进入 repr output、log、HTTP response、SQLite、fixture、audit evidence 或 Git history。测试可依据 invariant 12 使用明显虚构的 dummy value。调用方提供的通知内容属于 opaque data，会被持久化，并受 invariant 14 约束。
7. 持久化 error 必须 normalized、bounded 且 secret-safe。意外的 Provider exception 只能使用 generic error。
8. status projection 不得包含通知的 subject、title、body、metadata 或 Provider credential。
9. runtime database、WAL、log、backup 与 `.env` 文件绝不能提交到仓库。
10. terminal record 必须具有显式 retention policy 与经过测试的 purge path。durability 不等于无限期保留。
11. Core 必须保持 domain-agnostic。调用方特定的 monitoring、scheduling、scraping、workflow 或 business rule 不属于本工程。
12. example 与 test 只能使用明显虚构的 identity、content、endpoint 与 credential，并且绝不能访问 external network。
13. HTTP 默认只用于 loopback/private operation。non-loopback operation 必须显式 opt-in，并具备 authentication、TLS termination、network control、rate limiting 与 security review。
14. Gateway 不判断调用方提供的个人信息处理是否合法。文档必须保持 caller/operator responsibility boundary。
15. 新 Provider 必须记录 operator identity、已知情况下的 expected data region、可能的 cross-border transfer、支持的数据分类与 approval requirements。没有证据时不得声称 data residency。
16. Executor 的完成声明不能作为 closure evidence。必须检查 test、repository state、CI，以及独立的 diff/history review。
