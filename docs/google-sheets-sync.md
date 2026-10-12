# Google Sheets Sync

Google Sheets is used as the operator-friendly source for device configuration.
Runtime decisions use SQLite after sync.

## Expected Input

The required tab is `devices`. It should contain stable device identifiers,
display names, areas, device types, capabilities, and KNX mapping columns.
Scene rows must not be imported as devices.

## Runtime Flow

```text
Google Sheets devices tab
-> scripts/sync_google_sheet.py
-> smart_home_agent.services.device_sync.sync_devices()
-> SQLite devices table and sync_history
-> scripts/sync_homebridge.py
-> Homebridge accessory plan/apply
```

`scripts/sync_all.py` runs the Google sync first and then Homebridge
reconciliation. The Homebridge generator reads SQLite, not Google Sheets
directly.

## Credentials

Use a service-account JSON stored outside Git:

```env
GOOGLE_SHEET_ID=
GOOGLE_SHEET_TAB=devices
GOOGLE_CREDENTIALS_FILE=/path/outside/repo/google-credentials.json
```

Do not commit the JSON file. The code checks that the credential is a service
account and that a client email and private key are supplied. Set GOOGLE_EXPECTED_SERVICE_ACCOUNT to enforce a specific client email.

## Verification

Run on the Raspberry Pi with production environment loaded:

```bash
python -m scripts.doctor
python -m scripts.sync_google_sheet --database data/smart_home.db
python -m scripts.sync_homebridge --database data/smart_home.db --dry-run
```

The sync should preserve device IDs, aliases, rooms/areas, capabilities, and KNX
mapping fields. Invalid rows should fail the sync transaction and leave the
previous registry intact.

## Scheduling

Production may use a systemd timer to run the sync. Check with:

```bash
systemctl list-timers --all | grep sync
systemctl status smarthome-device-sync.timer
```

Keep the timer and service definitions outside Git unless they are sanitized
examples.
