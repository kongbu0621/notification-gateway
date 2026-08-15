# Privacy and mainland-China deployment boundary

This document is engineering guidance, not legal certification or legal advice. The gateway is jurisdiction-neutral software; the organization operating it remains responsible for its processing purpose, data, recipients, providers, retention, deployment, and notices.

## Public source code is not a public notification service

Publishing generic source code does not by itself mean that the repository processes end-user personal information. Operating the HTTP service, persisting requests, or delivering messages can do so when caller-supplied subject, title, body, metadata, identifiers, or provider evidence relates to an identified or identifiable person.

Caller-supplied notification fields are opaque and are persisted for durable delivery. The gateway does not reliably detect passwords, tokens, URLs, personal information, or other sensitive material embedded in those fields. Secret-safety guarantees for provider credentials and transport evidence do not convert caller content into safe or lawful data.

Keep repository history free of real credentials, personal email addresses, notification payloads, databases, logs, local paths, screenshots, and external task-sharing links. Contributors should use a GitHub-provided `users.noreply.github.com` commit address when they do not intend to publish an email address.

## Data minimization

- Prefer generic notification text plus an opaque event reference.
- Do not place passwords, access tokens, recovery codes, financial credentials, identity-document numbers, health data, precise location, unrestricted student profiles, or other unnecessary personal information in requests.
- Treat information about children under 14 and other sensitive personal information as higher risk.
- Do not use metadata as an unrestricted profile or evidence archive.
- A group-notification destination can disclose content to every group member; minimize content for that audience.

## Retention and rights

SQLite durability exists to deliver accepted work, not to create a permanent message archive. Operators must choose documented retention periods, run terminal-record purge, manage WAL/backup retention, restrict access, and establish a process for lawful access, correction, deletion, and incident requests. Pending or retryable records require an explicit operational decision before deletion because deletion may violate accepted-delivery authority.

## Providers and entrusted processing

Sending a notification transfers its content to a provider. Determine whether the provider is an entrusted processor, an independent recipient, or another role under the applicable arrangement. Record the purpose, categories, retention, protection measures, onward-processing rules, and deletion responsibilities in the applicable contract or policy.

The built-in WeCom-compatible adapter validates a Tencent-operated endpoint but does not prove that every deployment or message remains in a particular jurisdiction. Do not infer data residency from a hostname alone.

Before enabling any provider that may receive data outside mainland China, determine whether the request contains personal information or important data, perform the required impact assessment, notice/consent and contractual steps, and evaluate the applicable security-assessment, certification, or standard-contract threshold.

## Mainland-China legal framework to evaluate

Depending on the deployment and data, operators should evaluate at least:

- [Personal Information Protection Law](https://www.cac.gov.cn/2021-08/20/c_1631050028355286.htm): lawful basis, transparency, minimization, retention, sensitive/minor information, entrusted processing, individual rights, security measures, impact assessments, and cross-border provision.
- [Data Security Law](https://www.stats.gov.cn/gk/tjfg/xgfxfg/202503/t20250310_1958928.html): classification, full-lifecycle controls, risk monitoring, incident response, important data, and network-security obligations.
- [Cybersecurity Law, current text effective in 2026](https://www.cac.gov.cn/2025-12/29/c_1768735112911946.htm): network-operation and user-information protection obligations.
- [Network Data Security Management Regulations](https://www.cac.gov.cn/2024-09/30/c_1729384452307680.htm): network-data controls, recipient/processor contracts, privacy rules, individual requests, audits, incidents, and cross-border management.
- [Provisions on Facilitating and Regulating Cross-Border Data Flows](https://www.cac.gov.cn/2024-03/22/c_1712776611775634.htm): current exemptions and thresholds for security assessment, standard contracts, and certification.
- [Measures for the Administration of Personal Information Protection Compliance Audits](https://www.cac.gov.cn/2025-02/14/c_1741233507681519.htm): periodic compliance audits, audit scope, professional-institution requirements, and regulator-directed audits; effective May 1, 2025.
- [National Cybersecurity Incident Reporting Measures](https://www.cac.gov.cn/2025-09/15/c_1759583017717009.htm): incident classification, reporting channels, timing, content, and follow-up duties for network operators in mainland China; effective November 1, 2025.

Operators processing personal information of at least one million people should also evaluate the personal-information-protection-officer appointment and regulator reporting requirements. The threshold is deployment-specific; the project does not assume that every operator reaches it. See the [CAC reporting notice](https://www.cac.gov.cn/2025-07/18/c_1754553420421538.htm).

Public Internet operation from mainland-China infrastructure may also require ICP filing or another license and additional cybersecurity controls. The project does not determine or obtain those approvals for an operator.

## Deployment checklist

1. Classify request fields and provider destinations.
2. Establish a lawful basis and required notices/consents.
3. Minimize content and define retention before accepting production traffic.
4. Keep the database and backups on access-controlled, appropriately protected storage.
5. Keep the service on loopback/private networks unless a reviewed Internet edge supplies authentication, TLS, network controls, rate limiting, logging minimization, and incident response.
6. Contract with and assess providers; document possible cross-border transfers.
7. Test secret redaction, purge, recovery, and incident procedures.
8. Reassess when adding a provider, changing message content, expanding recipients, or changing deployment region.
