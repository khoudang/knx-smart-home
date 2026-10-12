from __future__ import annotations

import argparse
import os
from pathlib import Path
import time
from typing import Any

import requests
from requests import HTTPError, RequestException, Timeout

from smart_home_agent.services.user_repository import upsert_user
from smart_home_agent.storage.sqlite import connect, migrate


SENDER_FIELDS = ("sender_id", "user_id", "from_id", "uid", "id")


def main() -> None:
    load_env_file(Path(".env.production"))
    parser = argparse.ArgumentParser(description="One-shot Zalo owner onboarding for the configured bot API.")
    parser.add_argument("--database", default=os.environ.get("DATABASE_PATH", "data/smart_home.db"))
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--seed", action="store_true", help="Seed owner when exactly one new sender is found.")
    parser.add_argument("--discard-existing", action="store_true", help="Ignore already queued updates before waiting.")
    args = parser.parse_args()

    token = os.environ.get("ZALO_BOT_TOKEN")
    if not token:
        raise SystemExit("zalo_token_missing")

    base = f"https://bot-api.zaloplatforms.com/bot{token}"
    auth = requests.get(f"{base}/getMe", timeout=10)
    auth.raise_for_status()
    if not auth.json().get("ok"):
        raise SystemExit("zalo_token_invalid")
    print("[OK] Zalo token verified")
    print("zalo_adapter_mode=polling")

    offset = 0
    senders: dict[str, dict[str, str]] = {}

    initial = _post_get_updates(base, {"timeout": 1}, timeout=8)
    if initial.status == "another_poller_consuming_updates":
        print(initial.status)
        return
    if initial.status in {"invalid_getupdates_request", "token_not_authorized_for_getupdates", "network_timeout", "api_error"}:
        print(initial.status)
        return
    initial_updates = _updates(initial.json())
    if initial_updates:
        offset = max([int(update.get("update_id", 0)) for update in initial_updates], default=-1) + 1
        if not args.discard_existing:
            _collect_senders(initial_updates, senders)

    deadline = time.time() + max(args.timeout, 1)
    while time.time() < deadline and not senders:
        response = _post_get_updates(
            base,
            {"offset": offset, "timeout": min(10, max(1, int(deadline - time.time())))},
            timeout=15,
        )
        if response.status == "no_new_updates":
            continue
        if response.status in {
            "another_poller_consuming_updates",
            "invalid_getupdates_request",
            "token_not_authorized_for_getupdates",
            "network_timeout",
            "api_error",
        }:
            print(response.status)
            return
        updates = _updates(response.json())
        for update in updates:
            offset = max(offset, int(update.get("update_id", 0)) + 1)
        _collect_senders(updates, senders)

    if not senders:
        print("no_new_updates")
        return
    if len(senders) > 1:
        print("multiple_senders_found")
        for sender in senders.values():
            print(f"zalo_user_id={sender['sender_id']} sender_field={sender['field']} display_name={sender['display_name']}")
        return

    sender = next(iter(senders.values()))
    print(f"zalo_user_id={sender['sender_id']}")
    print(f"sender_field={sender['field']}")
    print(f"display_name={sender['display_name']}")

    if args.seed:
        conn = connect(args.database)
        try:
            migrate(conn)
            upsert_user(
                conn,
                channel="zalo",
                user_id=sender["sender_id"],
                display_name=sender["display_name"],
                role="owner",
                enabled=True,
            )
        finally:
            conn.close()
        print("zalo_owner_seeded")


def _updates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result", [])
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        return [result]
    return []


def _collect_senders(updates: list[dict[str, Any]], senders: dict[str, dict[str, str]]) -> None:
    for update in updates:
        found = _sender(update)
        if found:
            sender_id, field, display_name = found
            senders[sender_id] = {"sender_id": sender_id, "field": field, "display_name": display_name}


class ZaloPollingResponse:
    def __init__(self, status: str, payload: dict[str, Any] | None = None) -> None:
        self.status = status
        self.payload = payload or {}

    def json(self) -> dict[str, Any]:
        return self.payload


def _post_get_updates(base: str, body: dict[str, Any], timeout: int) -> ZaloPollingResponse:
    try:
        response = requests.post(f"{base}/getUpdates", json=body, timeout=timeout)
        response.raise_for_status()
    except Timeout:
        return ZaloPollingResponse("network_timeout")
    except HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 409:
            return ZaloPollingResponse("another_poller_consuming_updates")
        if exc.response is not None and exc.response.status_code in {401, 403}:
            return ZaloPollingResponse("token_not_authorized_for_getupdates")
        return ZaloPollingResponse("api_error")
    except RequestException:
        return ZaloPollingResponse("api_error")

    payload = response.json()
    if payload.get("ok") is True:
        return ZaloPollingResponse("ok", payload)

    error_code = payload.get("error_code")
    description = str(payload.get("description") or "").lower()
    if error_code == 408 or "timeout" in description:
        return ZaloPollingResponse("no_new_updates", payload)
    if error_code in {400, 404}:
        return ZaloPollingResponse("invalid_getupdates_request", payload)
    if error_code in {401, 403}:
        return ZaloPollingResponse("token_not_authorized_for_getupdates", payload)
    return ZaloPollingResponse("api_error", payload)


def _sender(update: dict[str, Any]) -> tuple[str, str, str] | None:
    message = update.get("message") if isinstance(update.get("message"), dict) else update
    candidates = []
    for key in ("from", "sender", "user", "chat"):
        value = message.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    candidates.append(message)
    for candidate in candidates:
        for field in SENDER_FIELDS:
            value = candidate.get(field)
            if value:
                display = candidate.get("display_name") or candidate.get("name") or candidate.get("username") or str(value)
                return str(value), field, str(display)
    return None


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


if __name__ == "__main__":
    main()
