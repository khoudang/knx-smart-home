# Homebridge integration

Choose one integration path for each device. Running multiple paths against the
same device can create duplicate accessories and conflicting commands.

## MQTT registry integration

`scripts/sync_homebridge.py` generates `mqttthing` accessories from SQLite.
`knx-bridge/bridge.py` consumes MQTT commands and publishes state using the registry's
KNX mappings. Install `mqttthing` in your Homebridge environment separately.

```bash
python -m scripts.sync_homebridge --database data/smart_home.db --dry-run
```

Inspect the generated plan before using `--apply`, which changes configuration and
restarts Homebridge. See [Google Sheets synchronization](google-sheets-sync.md) for
registry setup.

## Direct KNX plugin

The experimental [Homebridge KNX v3 package](../packages/homebridge-knx-v3/README.md)
connects directly to KNX. It uses the `KNXV3` platform and its own device schema;
it does not consume SQLite or `mqttthing` configuration.

```bash
npm ci --prefix packages/homebridge-knx-v3 --ignore-scripts
```

Switch and dimmer classes contain control logic. Temperature currently initializes
a characteristic without KNX feedback; other accessory types are scaffolds.
A type appearing in the accessory manager does not imply complete device support.

## Legacy v2 tooling

`deploy/homebridge-v2/setup.sh` installs and patches `homebridge-knx-v2` for DALI
acknowledgement, dimmer command handling, and scene pulses. It is a legacy utility,
not a v3 installer. The upstream v2 package version is not pinned, so patches must
be reviewed against the installed version. See the [v2 guide](../deploy/homebridge-v2/README.md).

## Existing installations

Preserve HomeKit identity and pairing. Check power group addresses, brightness,
color temperature, ETS feedback, and scene timing in a separate test environment
before changing an existing installation.

The v3 live test requires `KNX_LIVE_TEST=true`, `KNX_IP`, and the documented test
group-address variables. It performs real hardware writes and is never run by CI.
