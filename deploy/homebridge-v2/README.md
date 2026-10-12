# Legacy Homebridge v2 tooling

Installation and patching utilities for `homebridge-knx-v2` on Raspberry Pi/Linux.
For other integration paths, see the [Homebridge guide](../../docs/homebridge.md).

## Scope

The setup script installs dependencies and patches upstream files to adjust:

- DALI acknowledgement requests.
- Redundant dimmer ON commands.
- Scene auto-reset pulses, using an 800 ms interval.

The upstream package version is not pinned. Review every patch against the target
version before use. This utility is not an installer for the v3 package.

## Installation

Use a test environment first. The script changes system packages and installed
Node modules; preserve the existing Homebridge configuration and pairing data.

```bash
sudo bash setup.sh
```

Adapt `config.template.json` to your KNX interface, group addresses, and HomeKit
identity. The sample IP is reserved for documentation. Do not overwrite an existing
Homebridge configuration without reviewing and backing it up.

For a new installation only:

```bash
sudo cp config.template.json /var/lib/homebridge/config.json
sudo systemctl restart homebridge
```

## Known limitation

Real-time HomeKit updates after direct ETS writes have not been fully validated.
Check notification behavior and feedback mappings before adopting this integration.
