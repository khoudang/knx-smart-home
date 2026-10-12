from __future__ import annotations

from getpass import getpass
from pathlib import Path


PROMPTS = [
    ("HOME_ID", "Home ID/name", False),
    ("TIMEZONE", "Timezone", False),
    ("DATABASE_PATH", "SQLite database path", False),
    ("TELEGRAM_ENABLED", "Enable Telegram? true/false", False),
    ("TELEGRAM_BOT_TOKEN", "Telegram bot token", True),
    ("TELEGRAM_ALLOWED_USERS", "Telegram allowed users", False),
    ("ZALO_ENABLED", "Enable Zalo? true/false", False),
    ("ZALO_APP_ID", "Zalo app ID", False),
    ("ZALO_APP_SECRET", "Zalo app secret", True),
    ("ZALO_ACCESS_TOKEN", "Zalo access token", True),
    ("ZALO_REFRESH_TOKEN", "Zalo refresh token", True),
    ("DEEPSEEK_API_KEY", "DeepSeek API key", True),
    ("VOICE_ENABLED", "Voice chatbot runtime is disabled; keep false", False),
    ("GOOGLE_SHEET_ID", "Google Sheet ID", False),
    ("GOOGLE_SHEET_TAB", "Google Sheet tab", False),
    ("GOOGLE_CREDENTIALS_FILE", "Google credentials path", False),
    ("KNX_BRIDGE_URL", "KNX bridge URL", False),
    ("KNX_BRIDGE_TOKEN", "KNX bridge API token", True),
    ("KNX_DRY_RUN", "KNX dry-run true/false", False),
    ("SYNC_INTERVAL_SECONDS", "Sync interval seconds", False),
]

DEFAULTS = {
    "TIMEZONE": "Asia/Ho_Chi_Minh",
    "DATABASE_PATH": "data/smart_home.db",
    "TELEGRAM_ENABLED": "false",
    "ZALO_ENABLED": "false",
    "VOICE_ENABLED": "false",
    "GOOGLE_SHEET_TAB": "devices",
    "GOOGLE_CREDENTIALS_FILE": "secrets/google-credentials.json",
    "KNX_DRY_RUN": "true",
    "SYNC_INTERVAL_SECONDS": "3600",
}


def main() -> None:
    target = Path(".env")
    if target.exists():
        raise SystemExit(".env already exists; refusing to overwrite local secrets.")

    values: list[str] = []
    for key, label, secret in PROMPTS:
        default = DEFAULTS.get(key, "")
        suffix = f" [{default}]" if default else ""
        if secret:
            value = getpass(f"{label}{suffix}: ").strip()
        else:
            value = input(f"{label}{suffix}: ").strip()
        if not value:
            value = default
        values.append(f"{key}={value}")

    target.write_text("\n".join(values) + "\n", encoding="utf-8")
    Path("data").mkdir(exist_ok=True)
    Path("logs").mkdir(exist_ok=True)
    Path("secrets").mkdir(exist_ok=True)
    print("[OK] Created .env and local data/logs/secrets directories.")


if __name__ == "__main__":
    main()
