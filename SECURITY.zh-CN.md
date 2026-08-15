# 安全策略（Security policy）

[English](SECURITY.md) | **简体中文**

## 支持版本（Supported version）

工程处于 alpha 阶段时，仅支持 `0.1.x` 版本线。

## 报告安全漏洞（Reporting a vulnerability）

不要创建包含 credential、真实 notification payload、个人信息、database、带 Webhook URL 的 traceback，或可能危害实际 deployment 的 exploit details 的公开 Issue。

如果仓库的 **Security → Report a vulnerability** 可用，请使用 GitHub private vulnerability reporting。如果该渠道不可用，请先通过 private channel 联系 maintainer，再公开问题。任何可能已经泄露的 credential 都必须立即 rotate；删除 Git commit 或 Issue 并不能使该 credential 重新安全。

只提交经过清理的 reproduction data。必须把人员、组织、host、request identifier、token、URL、database row 与 message body 替换成明显虚构的 dummy value。

## 部署边界（Deployment boundary）

v0.1 HTTP service 面向 loopback 或受控 private network，不是 hardened public Internet edge。non-loopback deployment 必须具备 authentication、TLS termination、network access control、rate limiting、monitoring，并显式审查运营者承担的法律与安全义务。
