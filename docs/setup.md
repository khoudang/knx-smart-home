# Configuration and deployment

## Local environment

Follow the [quick start](../README.md#quick-start) to create a virtual environment,
install dependencies, and initialize SQLite. Run Python modules and the gateway
from the repository root.

Additional processes have separate dependencies:

```bash
python -m pip install -r knx-bridge/requirements.txt
python -m pip install -r scene-engine/requirements.txt
```

The API and gateway load `.env` from the working directory. Registry sync and the
diagnostic tool also read local environment files. The KNX bridge and scene engine
use their process environment: export the required settings or supply an
`EnvironmentFile` through your service manager. Do not assume every entry point
loads `.env` automatically.

Keep `KNX_DRY_RUN=true` and `MQTT_DRY_RUN=true` until device mappings, permissions,
and feedback have been checked. Never run two command consumers for the same devices
without defining which component owns each command namespace.

## Local MQTT broker

`infra/docker-compose.yml` provides a development broker. It permits anonymous
connections and is not a production broker configuration. Keep it on a trusted test
network; configure authentication, authorization, and network exposure before deployment.

## Google Sheets and Homebridge

Store service-account JSON outside the checkout. Set `GOOGLE_CREDENTIALS_FILE` to its
absolute path, then configure `GOOGLE_SHEET_ID`. Optionally set
`GOOGLE_EXPECTED_SERVICE_ACCOUNT` to restrict the credential identity.

```bash
python -m scripts.sync_google_sheet --database data/smart_home.db
python -m scripts.sync_homebridge --database data/smart_home.db --dry-run
```

Google sync writes to SQLite. Homebridge reconciliation produces a plan by default.
Its `--apply` option modifies Homebridge configuration and restarts the service;
review the plan and preserve a backup first.

## Service layout

Use immutable source releases and keep mutable state separately:

```text
deployment/
  releases/<commit>/
  current -> releases/<commit>
  shared/
    .env.production
    data/
    logs/
    secrets/
```

Set each service's `WorkingDirectory` to the release root and its environment file
to the shared runtime configuration. Create the Python environment and install
component dependencies for the target machine.

`scripts/deploy_release.sh` supports `--dry-run` and `--prepare-only` and does not
restart services automatically. Review its `--help` output before use. Preserve the
previous release and back up SQLite before migrations; application rollback alone
may not reverse a database migration.

## Network boundary

The API defaults to `127.0.0.1`. `/chat` trusts caller-supplied user identity and is
intended for a trusted gateway. A multi-host deployment needs an authentication
layer and network access controls. Dry-run flags are execution controls, not a
replacement for authentication.
