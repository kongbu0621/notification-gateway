# Security policy

## Supported version

`0.1.x` is the only supported line while the project is in alpha.

## Reporting a vulnerability

Do not open a public issue containing a credential, real notification payload, personal information, database, traceback with a webhook URL, or exploit details that would endanger a deployment.

Use GitHub private vulnerability reporting when the repository exposes **Security → Report a vulnerability**. If that channel is unavailable, contact the maintainer through a private channel before publishing details. Immediately rotate any credential that may have been exposed; deleting a Git commit or issue does not make a credential safe again.

Include only sanitized reproduction data. Replace people, organizations, hosts, request identifiers, tokens, URLs, database rows, and message bodies with unmistakable dummy values.

## Deployment boundary

The v0.1 HTTP service is designed for loopback or a controlled private network. It is not a hardened public Internet edge. A non-loopback deployment requires authentication, TLS termination, network access control, rate limiting, monitoring, and an explicit review of the operator's legal and security obligations.
