# Homebridge KNX v3

Experimental direct KNX integration for Homebridge, included in KNX Smart Home.
The plugin registers `homebridge-knx-v3` with platform name `KNXV3`.

## Requirements

- Homebridge 2.x, as declared in the package peer dependencies.
- Node.js 20 or later; repository CI uses Node.js 22.
- A KNX IP interface and installation-specific group-address mappings.

## Installation

From this directory:

```bash
npm ci --ignore-scripts
npm pack
```

Install the local package in a separate Homebridge test environment. This repository
does not provide a verified npm release of the plugin.

## Configuration

Add a platform entry to your Homebridge configuration. Replace the documentation
IP address and example group addresses with values from your installation.

```json
{
  "platform": "KNXV3",
  "name": "KNX",
  "knx": {"host": "192.0.2.10", "port": 3671},
  "devices": [
    {"name": "Example switch", "type": "switch", "control": "1/0/1", "status": "1/0/2"}
  ]
}
```

## Implementation status

| Accessory | Current implementation |
| --- | --- |
| Switch | Control logic present; validate status feedback on hardware. |
| Dimmer | Brightness, switching, optional power and color-temperature logic present. |
| Temperature | Initial characteristic value only; KNX feedback is not implemented. |
| Other registered types | Scaffolds requiring implementation and integration tests. |

Dimmers use `control`, `level`, optional `power.control`, and `cct` fields.
Color-temperature conversion requires device-specific validation.
Configuration for `mqttthing` or the v2 plugin is not interchangeable with this schema.

## Hardware testing

`test-knx.js` performs real KNX writes. It requires all of:

- `KNX_LIVE_TEST=true`
- `KNX_IP`
- `KNX_TEST_POWER_GA`
- `KNX_TEST_CONTROL_GA`
- `KNX_TEST_BRIGHTNESS_GA`

CI checks syntax only and never runs this script. Use a dedicated test device and
verify every address before enabling the test.

## License

[MIT](LICENSE).
