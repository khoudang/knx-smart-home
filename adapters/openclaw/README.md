# OpenClaw adapter

Optional OpenClaw skills, KNX middleware, Homebridge generators, and a Siri webhook
for KNX Smart Home. These tools run independently of the core agent and are not
clients of its HTTP API. Assign separate command ownership before running them
alongside the main control path.

## Setup

Run from this directory:

```bash
npm ci --ignore-scripts
python -m pip install -r requirements.txt
mkdir -p config/cache logs
cp .env.example .env
```

Keep `KNX_DRY_RUN=true`. Direct Node/Python commands require exported environment
variables; `start_bot.sh` loads the local `.env` file.

Set `GOOGLE_SHEET_ID` and `GOOGLE_CREDENTIALS_FILE` to your spreadsheet and an
absolute credential path outside Git. Create the required `KNX`, `permissions`,
`faq`, and `KNX_history` tabs; consult the skills for their column mappings.

```bash
node skills/fetch_devices.js --refresh
```

This creates a local device cache. Control is rejected when the cache is absent.
The JSON examples under `config/` are empty templates, not a working installation.

Configure the installed OpenClaw runtime using its own setup tools and register
skills according to that version. Runtime agent configuration and memory are local
state and must remain outside version control.

## Permissions and Siri

`OPENCLAW_ADMIN_USERS` is a comma-separated list of `channel:userId` entries.
There are no default administrators. `init_permissions_sheet.js` initializes only
the administrators supplied through this setting.

The Siri webhook requires `SIRI_SECRET_TOKEN` and listens on loopback.
`generate_hb.py` produces runtime configuration containing this token; do not
publish or share its output. KNX middleware defaults to dry-run and requires
explicit network configuration and a valid local registry for live operation.

These tools do not implement the complete policy of the core agent. Permission
management, history deletion, and credential updates have real effects when run.
Token updates target the user's local OpenClaw runtime and must not be included
in source backups.

## Compatibility

OpenClaw schemas and skill registration depend on the installed runtime version.
Compatibility has not been established across all releases. Test this adapter
independently before connecting it to an operational installation.
