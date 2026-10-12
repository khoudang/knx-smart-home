# Security Model

## Fail-Closed Defaults

The production safety baseline is:

```env
KNX_DRY_RUN=true
MQTT_DRY_RUN=true
```

Device-control tests may validate intent routing, resolver behavior,
permissions, and bridge logging, but must not send real KNX writes while these
flags remain true.

## LLM Boundary

The LLM may classify:

- domain
- intent
- natural language entity text
- confidence
- clarification need

The LLM must not decide:

- KNX group addresses
- MQTT topics
- device IDs
- owner/admin roles
- direct database writes
- whether to bypass confirmation

## Access Control

Unknown users are denied by default and may request access. Owner/admin actions
use deterministic database functions. Destructive actions create pending
confirmations and must not execute from a raw fallback parser.

## Secret Handling

Never commit:

- `.env` or `.env.production`
- service-account JSON files
- Telegram/Zalo/DeepSeek tokens
- SQLite production databases
- logs or backups
- MQTT/KNX credentials

If a real secret has ever appeared in Git history, rotate it and clean history
before publishing the repository.

## Live KNX Cutover

Live KNX writes require a separate checklist:

- dry-run verification is complete
- one bridge subscriber owns the command namespace
- rollback is prepared
- sensitive device policies are enabled
- journal confirms no unexpected `GroupValueWrite`
- the owner explicitly approves disabling dry-run
