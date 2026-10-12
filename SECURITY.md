# Security policy

## Reporting a vulnerability

Report vulnerabilities through [GitHub private vulnerability reporting](https://github.com/khoudang/knx-smart-home/security/advisories/new).
Do not post credentials, private installation details, or exploit instructions in a public issue.

Include the affected commit, component, impact, and a minimal reproduction using
synthetic data. There is no guaranteed response-time or long-term-support commitment.
Security fixes currently target the default branch.

## Deployment boundaries

The agent API trusts identity supplied by a chat gateway. Keep it on loopback or
behind an authenticated, trusted network boundary; do not expose it directly to the
Internet. The sample MQTT broker allows anonymous connections for local development.
Production deployments require their own broker authentication and authorization.

Core agent access-control policy is not automatically applied to the direct
Homebridge plugin or standalone OpenClaw tools. Review each execution path separately.
See the [security model](docs/security-model.md) for details.

## Credentials and runtime state

Keep environment files, service-account keys, tokens, customer data, installation
addresses, databases, logs, and backups outside version control. Commit synthetic
examples only. Generated configuration can contain credentials even when its name
does not suggest that it does.

Run `python scripts/check_public_tree.py` after staging changes. CI also scans Git
history with Gitleaks. These checks reduce accidental disclosure but do not replace
manual review. If a credential is exposed, revoke or rotate it at the provider.
Deleting a file or commit does not invalidate a credential.

## Hardware operations

Start with `KNX_DRY_RUN=true` and `MQTT_DRY_RUN=true`. Validate group addresses,
permissions, sensitive-device policies, and state feedback before live operation.
Never include live hardware writes in routine automated tests.
