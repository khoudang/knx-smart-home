# AICONTROL Smart Home

**A modular KNX automation platform with conversational control and Homebridge integration.**

[![CI](https://github.com/khoudang/aicontrol-smart-home/actions/workflows/ci.yml/badge.svg)](https://github.com/khoudang/aicontrol-smart-home/actions/workflows/ci.yml)
[![License: ISC](https://img.shields.io/badge/License-ISC-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Node.js 22+](https://img.shields.io/badge/Node.js-22%2B-339933?logo=nodedotjs&logoColor=white)

AICONTROL connects Telegram and Zalo to a Python automation agent, a local device registry,
and a KNX control bridge. Google Sheets provides a configurable source for device data,
while Homebridge integrations expose supported devices to Apple Home.

Language models interpret requests. Application code resolves device identifiers,
checks permissions, and validates control operations against the registry.

[Quick start](#quick-start) ? [Configuration](#configuration) ? [Integrations](#integrations) ? [Development](#development)

## Features

- **Conversational control** through Telegram and Zalo gateways.
- **Role-based access** with owner management, access requests, and guest expiration.
- **Local device registry** backed by SQLite, with Google Sheets synchronization.
- **KNX connectivity** through an MQTT bridge with state feedback.
- **Apple Home integration** through Homebridge MQTT accessories or a direct KNX plugin.
- **Optional OpenClaw tools** for skills, middleware, and a Siri webhook.
- **Dry-run operation** for reviewing behavior before enabling hardware writes.

## Architecture

```text
Telegram / Zalo
       |
       v
  Chat gateway --> HTTP API --> Python agent --> SQLite registry
                                    |           Access control
                                    v
                               MQTT broker
                                    |
                                    v
                               KNX bridge --> KNX devices

Google Sheets --> Registry sync --> SQLite --> Homebridge mqttthing --> Apple Home
KNX devices <-------------------------- Direct Homebridge KNX plugin --> Apple Home
```

Components run as separate processes with their own dependencies. Choose one Homebridge
backend for each device to avoid duplicate accessories and conflicting commands.

## Quick start

### Prerequisites

- Python 3.11 or later.
- Node.js 22 or later and npm.
- Git.
- Linux or Raspberry Pi OS for deployment. Windows is supported for development;
  Bash deployment scripts require Linux or WSL.

### 1. Install

```bash
git clone https://github.com/khoudang/aicontrol-smart-home.git
cd aicontrol-smart-home
python -m venv .venv
```

Activate the virtual environment:

```bash
# Linux / macOS
source .venv/bin/activate
```

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Install the agent and chat gateway dependencies:

```bash
python -m pip install -r ai-agent/requirements.txt
npm ci --prefix chatbot-gateway --ignore-scripts
```

Optional integrations have additional dependencies; see [Integrations](#integrations).

### 2. Configure

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

On PowerShell, use `Copy-Item .env.example .env`.

Keep the initial configuration in dry-run mode:

```dotenv
KNX_DRY_RUN=true
MQTT_DRY_RUN=true
TELEGRAM_ENABLED=false
ZALO_ENABLED=false
API_HOST=127.0.0.1
```

Add your provider credentials and device configuration when enabling each integration.
Keep credentials in local environment files or an external secrets directory.

### 3. Initialize and run

Run these commands from the repository root:

```bash
python -m scripts.migrate --db data/smart_home.db
python ai-agent/api.py
```

The health endpoint is available at `http://127.0.0.1:5000/health`.

In a second terminal, create the first owner using your Telegram user ID:

```bash
python -m scripts.manage_users --database data/smart_home.db add --channel telegram --user-id YOUR_USER_ID --role owner
```

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ENABLED=true` in `.env`, then start the gateway
from the repository root:

```bash
node chatbot-gateway/index.js
```

The API and gateway load `.env` on startup. Restart the relevant process after changing
configuration. Import or configure devices before testing device-control requests.

## Configuration

| Setting | Purpose | Default |
| --- | --- | --- |
| `DATABASE_PATH` | SQLite registry location | `data/smart_home.db` |
| `API_HOST` / `PORT` | Agent API bind address and port | `127.0.0.1` / `5000` |
| `TELEGRAM_ENABLED` / `ZALO_ENABLED` | Enable chat channels | `false` |
| `KNX_DRY_RUN` / `MQTT_DRY_RUN` | Prevent live control writes | `true` |
| `MQTT_HOST` / `MQTT_PORT` | MQTT broker connection | `localhost` / `1883` |
| `GOOGLE_SHEET_ID` | Device registry spreadsheet | User supplied |
| `GOOGLE_CREDENTIALS_FILE` | External service-account JSON path | User supplied |
| `GOOGLE_EXPECTED_SERVICE_ACCOUNT` | Optional service-account identity restriction | Unset |

See [.env.example](.env.example) for the available settings and
[deployment guide](docs/setup.md) for process-specific environment handling.
The KNX bridge and scene engine use their process environment; configure it explicitly
when starting them or running them as services.

The HTTP API binds to loopback by default. Its `/chat` endpoint trusts identity supplied
by the caller and must remain behind a trusted gateway. Multi-host deployments require
an authentication layer and appropriate network access controls.

## Integrations

| Integration | Location | Notes |
| --- | --- | --- |
| Google Sheets | [Guide](docs/google-sheets-sync.md) | Synchronize device data into the local registry. |
| MQTT / KNX | `knx-bridge/` | Install `knx-bridge/requirements.txt`; configure a broker and KNX interface. |
| Homebridge MQTT | [Guide](docs/homebridge.md) | Generate accessories from the registry for `mqttthing`. |
| Homebridge KNX v3 | [Plugin](packages/homebridge-knx-v3/README.md) | Experimental direct KNX integration; some accessory types are scaffolds. |
| OpenClaw | [Adapter](adapters/openclaw/README.md) | Standalone optional tools requiring local OpenClaw configuration. |
| Homebridge v2 | `deploy/homebridge-v2/` | Legacy installation and patching utilities for the v2 plugin. |
| Scene engine | `scene-engine/` | Separate component; scene execution through the agent is currently disabled. |

Install root Node dependencies with `npm ci --ignore-scripts` when using integrations
that require them. Follow each component's guide before enabling it.

## Project layout

```text
smart_home_agent/             Core agent, skills, access control, and storage
ai-agent/                    HTTP API entry point
chatbot-gateway/              Telegram and Zalo adapters
knx-bridge/                  MQTT-to-KNX bridge
scene-engine/                Scene processing
adapters/openclaw/           Optional OpenClaw and Siri tools
packages/homebridge-knx-v3/  Direct Homebridge KNX plugin
deploy/homebridge-v2/        Legacy Homebridge deployment utilities
scripts/                    Migration, synchronization, and administration
tests/                      Unit tests and fixtures
docs/                       Integration and deployment guides
```

## Development

Run the unit tests and source checks from the repository root:

```bash
python -m unittest discover -s tests
python scripts/check_public_tree.py
git diff --check
```

The publication checker examines tracked files. Stage new files before running it.
GitHub Actions runs unit tests, Python/JavaScript/Bash syntax checks, publication checks,
and a Gitleaks scan of Git history. Hardware tests are opt-in and require explicit configuration.

For contributions, describe the behavior being changed, include relevant checks, and
use synthetic fixtures rather than deployment data.

## Project status

The project is under active development. Automated checks cover source validation and
simulated behavior; compatibility with a specific KNX installation, Homebridge deployment,
or OpenClaw runtime requires integration testing. Some optional components are experimental,
and dependency pinning is not yet complete across all runtimes.

Start in dry-run mode and validate device mappings, permissions, and state feedback before
enabling hardware control. See [deployment guidance](docs/setup.md) and the
[security policy](SECURITY.md) for operational details.

## License

The core project is licensed under the [ISC License](LICENSE).
The Homebridge KNX v3 package is licensed separately under the
[MIT License](packages/homebridge-knx-v3/LICENSE).
