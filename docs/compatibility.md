# Compatibility and project status

## Development baseline

| Environment | Validation |
| --- | --- |
| Python 3.12 / Node.js 22 / Linux | Main GitHub Actions environment. |
| Windows / Python 3.12 | Local unit suite and source checks. |
| Python 3.11 | Declared minimum; unit suite included in CI. |
| Raspberry Pi / KNX hardware | Deployment target; full hardware validation is outstanding. |

## Component maturity

| Component | Status |
| --- | --- |
| Core agent and SQLite registry | Behavioral tests for routing, permissions, and registry operations. |
| HTTP API | Local initialization smoke check; trusted-gateway deployment model. |
| Telegram and Zalo | Gateway implementation present; live accounts require separate verification. |
| Google Sheets | Sync implementation and validation; live credentials supplied by the operator. |
| MQTT / KNX bridge | Dry-run defaults; end-to-end hardware verification required. |
| Homebridge MQTT | Registry-based configuration planning; validate in the target installation. |
| Direct Homebridge v3 | Experimental; switch/dimmer logic, incomplete remaining accessory types. |
| Scene engine | Separate component; scene execution through the agent is disabled. |
| OpenClaw | Optional standalone adapter; runtime-version compatibility requires testing. |
| Homebridge v2 tooling | Legacy upstream patches; upstream package version is not pinned. |

Documentation and automated checks do not establish production readiness. A passing
unit suite does not demonstrate hardware compatibility or provider availability.

## Language coverage

Public documentation is in English. Many runtime responses and legacy tools use
Vietnamese; English documentation does not imply complete English intent or response
coverage. Validate the required language behavior for your deployment.

## Dependency policy

Node components with lockfiles use `npm ci`. Python requirement files currently mix
pinned and unpinned packages; deployments should record their resolved environment
and validate updates before rollout. The root Node package is a private tooling
package, not a published npm distribution. A package version field alone does not
constitute a project release.
