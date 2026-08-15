# 隐私与中国大陆部署边界（Privacy and mainland-China deployment boundary）

[English](privacy-and-mainland-china.md) | **简体中文**

本文档是工程指导（engineering guidance），不构成法律认证（legal certification）或法律意见（legal advice）。Gateway 是 jurisdiction-neutral software；运营组织仍须对 processing purpose、data、recipient、Provider、retention、deployment 与 notice 负责。

## 公开源代码不等于公开通知服务（Public source code is not a public notification service）

公开通用源代码本身不代表仓库正在处理终端用户个人信息。但在实际运行 HTTP service、持久化 request 或投递 message 时，如果调用方提供的 subject、title、body、metadata、identifier 或 Provider evidence 与已识别或可识别自然人相关，就可能构成个人信息处理。

调用方提供的通知字段属于 opaque caller data，并会为了 durable delivery 被持久化。Gateway 无法可靠检测这些字段中嵌入的 password、token、URL、个人信息或其他敏感材料。Provider credential 与 transport evidence 的 secret-safety guarantee，不会自动使调用方内容变得安全或合法。

仓库历史不得包含真实 credential、个人邮箱、notification payload、database、log、本机路径、screenshot 或外部 task-sharing link。贡献者如果不希望公开邮箱，应使用 GitHub 提供的 `users.noreply.github.com` commit address。

## 数据最小化（Data minimization）

- 优先使用 generic notification text 与 opaque event reference。
- 不要在 request 中放入 password、access token、recovery code、financial credential、身份证件号码、health data、precise location、无限制的学生档案或其他非必要个人信息。
- 将不满 14 周岁未成年人的信息及其他敏感个人信息（sensitive personal information）视为更高风险数据。
- 不要把 metadata 用作无限制的 profile 或 evidence archive。
- 群通知 destination 可能向所有群成员披露内容；必须针对该 audience 进一步最小化。

## 数据保留与个人权利（Retention and rights）

SQLite durability 的目的，是投递已接受的任务，而不是建立永久消息档案。运营者必须选择并记录 retention period，运行 terminal-record purge，管理 WAL/backup retention，限制访问，并建立合法的 access、correction、deletion 与 incident request 流程。

删除 pending 或 retryable record 前必须作出显式 operational decision，因为直接删除可能违反已经接受的 delivery authority。

## Provider 与受托处理（Providers and entrusted processing）

发送通知会把内容传输给 Provider。运营者必须依据具体安排判断 Provider 属于受托处理者（entrusted processor）、独立 recipient，还是其他角色，并在适用 contract 或 policy 中记录 purpose、data category、retention、protection measure、onward-processing rule 与 deletion responsibility。

内置的 WeCom-compatible Adapter 会校验 Tencent-operated endpoint，但这不能证明每个 deployment 或 message 都留在特定 jurisdiction。不得仅根据 hostname 推断 data residency。

启用任何可能在中国大陆以外接收数据的 Provider 前，必须判断 request 是否包含个人信息或重要数据，完成所需的个人信息保护影响评估（impact assessment）、notice/consent 与 contract steps，并评估适用的 security assessment、personal information protection certification 或 standard contract threshold。

## 需要评估的中国大陆法律框架（Mainland-China legal framework to evaluate）

运营者应根据具体 deployment 与 data 至少评估：

- [《中华人民共和国个人信息保护法》（Personal Information Protection Law, PIPL）](https://www.cac.gov.cn/2021-08/20/c_1631050028355286.htm)：lawful basis、transparency、data minimization、retention、敏感个人信息与未成年人信息、受托处理、个人权利、安全措施、影响评估及个人信息出境。
- [《中华人民共和国数据安全法》（Data Security Law, DSL）](https://www.stats.gov.cn/gk/tjfg/xgfxfg/202503/t20250310_1958928.html)：data classification、full-lifecycle control、risk monitoring、incident response、重要数据与网络安全义务。
- [《中华人民共和国网络安全法》（Cybersecurity Law，2026 年施行的现行文本）](https://www.cac.gov.cn/2025-12/29/c_1768735112911946.htm)：network operation 与 user information protection obligations。
- [《网络数据安全管理条例》（Network Data Security Management Regulations）](https://www.cac.gov.cn/2024-09/30/c_1729384452307680.htm)：network data control、recipient/processor contract、privacy rule、individual request、audit、incident 与 cross-border management。
- [《促进和规范数据跨境流动规定》（Provisions on Facilitating and Regulating Cross-Border Data Flows）](https://www.cac.gov.cn/2024-03/22/c_1712776611775634.htm)：security assessment、standard contract 与 certification 的现行豁免和数量 threshold。
- [《个人信息出境认证办法》（Measures for Personal Information Export Certification）](https://www.cac.gov.cn/2025-10/17/c_1762449728720008.htm)：certification route 的适用条件、申请、认证、监督与 certificate requirements；自 2026 年 1 月 1 日起施行。
- [《个人信息保护合规审计管理办法》（Measures for the Administration of Personal Information Protection Compliance Audits）](https://www.cac.gov.cn/2025-02/14/c_1741233507681519.htm)：periodic compliance audit、audit scope、professional institution 要求和监管部门要求的审计；自 2025 年 5 月 1 日起施行。
- [《国家网络安全事件报告管理办法》（National Cybersecurity Incident Reporting Measures）](https://www.cac.gov.cn/2025-09/15/c_1759583017717009.htm)：中国大陆 network operator 的 incident classification、reporting channel、time limit、report content 与 follow-up obligations；自 2025 年 11 月 1 日起施行。

处理至少 100 万人个人信息的运营者，还应评估个人信息保护负责人（Personal Information Protection Officer, PIPO）的指定与监管部门报送要求。该 threshold 取决于具体 deployment；本工程不假定每个运营者都达到门槛。参见[国家网信办报送公告（CAC reporting notice）](https://www.cac.gov.cn/2025-07/18/c_1754553420421538.htm)。

在中国大陆基础设施上向 public Internet 提供服务，还可能需要 ICP 备案或其他许可，以及额外的 cybersecurity controls。本工程不会替运营者判断或取得这些 approval。

以上列表并非穷尽。运营者还必须识别适用于具体 deployment 的 data-specific、sector-specific、recipient-specific 与 service-specific rules。征求意见稿（consultation draft）在正式通过并生效前，不在本文中作为现行 operative requirement。

## 部署检查清单（Deployment checklist）

1. 对 request fields 与 Provider destination 进行 data classification。
2. 建立 lawful basis，并完成所需 notice/consent。
3. 在接收 production traffic 前最小化内容并定义 retention。
4. 将 database 与 backup 放在访问受控、保护措施适当的 storage 上。
5. 服务应保持在 loopback/private network；如需公网开放，必须由经过审查的 Internet edge 提供 authentication、TLS、network control、rate limiting、logging minimization 与 incident response。
6. 与 Provider 签订适用 contract、完成评估，并记录可能的 cross-border transfer。
7. 测试 secret redaction、purge、recovery 与 incident procedure。
8. 新增 Provider、改变 message content、扩大 recipient 或改变 deployment region 时重新评估。
