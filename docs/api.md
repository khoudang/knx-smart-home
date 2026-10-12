# Agent HTTP API

The Flask API is a trusted-gateway interface, not a public client API. It defaults
to `127.0.0.1:5000`. Run it with `python ai-agent/api.py` from the repository root.
The current entry point uses Flask's development server; use an appropriate WSGI
service configuration for deployment.

## Trust model

The gateway supplies the channel and user identity. `/chat` does not independently
authenticate that identity. Keep the API on loopback or behind an authenticated
network boundary. Application-level user permissions do not protect against an
untrusted caller impersonating a different user at this boundary.

## Health

```http
GET /health
```

Returns HTTP 200 with service status, dry-run settings, and whether a database path
is configured. This is an initialization check, not a KNX, broker, or provider
connectivity check.

```bash
curl http://127.0.0.1:5000/health
```

## Chat

```http
POST /chat
Content-Type: application/json
```

Example trusted-gateway payload:

```json
{
  "message": "help",
  "source": "telegram",
  "source_message_id": "example-message-001",
  "user_id": "example-user",
  "display_name": "Example User"
}
```

The gateway should supply a stable source message ID to support duplicate handling.
Actual actions depend on the registered user, resolved devices, permissions, and
execution policy.

Successful HTTP responses contain:

| Field | Meaning |
| --- | --- |
| `reply` | User-facing response text. |
| `status` | Application outcome, including denied or pending operations. |
| `request_id` | Identifier for tracing the request. |
| `executed` | Whether the response records an executed operation. |
| `notifications` | Additional messages produced by the agent. |

HTTP 200 does not mean a device was controlled. Inspect `status` and `executed`.
A missing message returns HTTP 400. Unhandled request failures return HTTP 500;
restrict API access and avoid sharing unsanitized error bodies.

## Disabled endpoints

Legacy direct-tool endpoints under `/api/` return HTTP 410. Device-control requests
must pass through the orchestrator instead of bypassing registry and policy checks.
