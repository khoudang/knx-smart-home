# Architecture

AICONTROL separates language understanding from execution. Chat adapters send
messages to the AI agent API. The agent classifies intent with an LLM, validates
the result against a strict schema, resolves entities from SQLite, checks
permissions and safety policy, then calls deterministic skill executors.

## Runtime Flow

```text
chatbot-gateway
-> ai-agent /chat
-> Orchestrator
-> IntentClassifier
-> IntentRouter
-> Skill executor
-> SQLite, Google Sheets, Open-Meteo, web search, or KNX bridge client
```

The LLM is not allowed to provide trusted identifiers such as KNX group
addresses, MQTT topics, database IDs, or owner role decisions. Those values come
from code and database state.

## Migrated Domains

- `access_control`
- `weather`
- `web_search`
- `scene`
- `device_control`

`general_chat` intentionally remains a conversational fallback.

## Production Deployment Shape

Production code is deployed through immutable release directories. Runtime
state is kept outside Git and symlinked in:

```text
smart-home-deploy/current -> smart-home-deploy/releases/<commit>
smart-home-agent/.env.production
smart-home-agent/secrets/
smart-home-agent/data/
smart-home-agent/.venv/
smart-home-agent/chatbot-gateway/node_modules/
```

This keeps rollback simple and prevents runtime files from being archived into
source releases.
