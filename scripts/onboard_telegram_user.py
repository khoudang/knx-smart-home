from __future__ import annotations

import argparse
import os
import time
from typing import Any

import requests
from requests import HTTPError

from smart_home_agent.services.user_repository import upsert_user
from smart_home_agent.storage.sqlite import connect, migrate


def main() -> None:
    parser = argparse.ArgumentParser(description="One-shot Telegram owner onboarding.")
    parser.add_argument("--database", default=os.environ.get("DATABASE_PATH", "data/smart_home.db"))
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--seed", action="store_true", help="Seed owner when exactly one new sender is found.")
    args = parser.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("telegram_token_missing")

    base = f"https://api.telegram.org/bot{token}"
    get_me = requests.get(f"{base}/getMe", timeout=10)
    get_me.raise_for_status()
    if not get_me.json().get("ok"):
        raise SystemExit("telegram_token_invalid")
    print("[OK] Telegram token verified")

    try:
        initial = requests.get(f"{base}/getUpdates", params={"timeout": 0, "allowed_updates": '["message"]'}, timeout=10)
        initial.raise_for_status()
    except HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 409:
            print("telegram_polling_conflict")
            return
        print("telegram_get_updates_failed")
        return
    updates = initial.json().get("result", [])
    offset = max([update.get("update_id", 0) for update in updates], default=-1) + 1

    deadline = time.time() + max(args.timeout, 1)
    senders: dict[str, dict[str, Any]] = {}
    while time.time() < deadline and not senders:
        try:
            response = requests.get(
                f"{base}/getUpdates",
                params={"offset": offset, "timeout": min(10, max(1, int(deadline - time.time()))), "allowed_updates": '["message"]'},
                timeout=15,
            )
            response.raise_for_status()
        except HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 409:
                print("telegram_polling_conflict")
                return
            print("telegram_get_updates_failed")
            return
        for update in response.json().get("result", []):
            offset = max(offset, update.get("update_id", 0) + 1)
            sender = update.get("message", {}).get("from")
            if sender and not sender.get("is_bot"):
                user_id = str(sender["id"])
                senders[user_id] = {
                    "user_id": user_id,
                    "display_name": _display_name(sender),
                    "username": sender.get("username"),
                }

    if not senders:
        print("waiting_for_user_message")
        return
    if len(senders) > 1:
        print("multiple_senders_found")
        for sender in senders.values():
            print(f"telegram_user_id={sender['user_id']} display_name={sender['display_name']} username={sender.get('username') or ''}")
        return

    sender = next(iter(senders.values()))
    print(f"telegram_user_id={sender['user_id']}")
    print(f"display_name={sender['display_name']}")
    if sender.get("username"):
        print(f"username={sender['username']}")

    if args.seed:
        conn = connect(args.database)
        try:
            migrate(conn)
            upsert_user(
                conn,
                channel="telegram",
                user_id=sender["user_id"],
                display_name=sender["display_name"],
                role="owner",
                enabled=True,
            )
        finally:
            conn.close()
        print("telegram_owner_seeded")


def _display_name(sender: dict[str, Any]) -> str:
    parts = [sender.get("first_name"), sender.get("last_name")]
    return " ".join(str(part).strip() for part in parts if part).strip() or sender.get("username") or str(sender["id"])


if __name__ == "__main__":
    main()
