from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from smart_home_agent.integrations.google_sheets import (
    EXPECTED_SERVICE_ACCOUNT,
    GoogleSheetError,
    assert_tab_exists,
    build_sheets_service,
    fetch_tab_rows,
    google_credentials_path,
    load_service_account_info,
    valid_service_account_info,
)


def truthy(value: str | None) -> bool:
    return (value or "").lower() in {"1", "true", "yes", "on"}


def main() -> None:
    load_env_file(Path(".env.production"))
    load_env_file(Path(".env"))
    expected_account = os.environ.get("GOOGLE_EXPECTED_SERVICE_ACCOUNT", EXPECTED_SERVICE_ACCOUNT)
    knx_dry_run = truthy(os.environ.get("KNX_DRY_RUN", "true"))
    mqtt_dry_run = truthy(os.environ.get("MQTT_DRY_RUN", os.environ.get("KNX_DRY_RUN", "true")))

    print(f"[{'DRY-RUN' if knx_dry_run else 'OK'}] KNX_DRY_RUN={str(knx_dry_run).lower()}")
    print(f"[{'DRY-RUN' if mqtt_dry_run else 'OK'}] MQTT_DRY_RUN={str(mqtt_dry_run).lower()}")
    print(f"[{'OK' if os.environ.get('MQTT_HOST') else 'FAIL'}] MQTT_HOST")
    print(f"[{'OK' if os.environ.get('MQTT_PORT') else 'FAIL'}] MQTT_PORT")

    if knx_dry_run and not os.environ.get("KNX_BRIDGE_URL"):
        print("[SKIP] KNX_BRIDGE_URL not required while KNX_DRY_RUN=true")
    else:
        print(f"[{'OK' if os.environ.get('KNX_BRIDGE_URL') else 'FAIL'}] KNX_BRIDGE_URL")

    if os.environ.get("DEEPSEEK_API_KEY"):
        print("[OK] DEEPSEEK_API_KEY configured")
    else:
        print("[SKIP] DEEPSEEK_API_KEY not required for local parser/dry-run tests")

    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    sheet_tab = os.environ.get("GOOGLE_SHEET_TAB", "devices")
    if sheet_id:
        print("[OK] GOOGLE_SHEET_ID configured")
    else:
        print("[SKIP] GOOGLE_SHEET_ID not required unless running live sheet sync")

    voice_enabled = truthy(os.environ.get("VOICE_ENABLED", "false"))
    print(f"[{'FAIL' if voice_enabled else 'OK'}] VOICE_ENABLED=false")

    credentials_path = google_credentials_path()
    if credentials_path and credentials_path.exists():
        print("[OK] Google credentials file")
        try:
            info = load_service_account_info(credentials_path)
            client_email = info.get("client_email")
            if valid_service_account_info(info, expected_account):
                print("[OK] Google client email")
            else:
                print(f"[FAIL] Google client email: expected {expected_account or 'configured service account'}")
        except Exception as exc:
            print(f"[FAIL] Google credentials JSON: {type(exc).__name__}")

        if sheet_id:
            try:
                service = build_sheets_service(credentials_path)
                assert_tab_exists(service, sheet_id, sheet_tab)
                print("[OK] Google Sheet accessible")
                print(f"[OK] devices tab: {sheet_tab}")
                fetch_tab_rows(service, sheet_id, sheet_tab)
                print("[OK] Live sync")
            except GoogleSheetError as exc:
                if exc.code == "permission_denied":
                    print(f"[FAIL] Google Sheet accessible: permission denied (permission_denied) for {expected_account or 'configured service account'}")
                elif exc.code == "tab_not_found":
                    print("[FAIL] devices tab: tab_not_found")
                    if exc.visible_tabs:
                        print("visible_tabs=" + ",".join(exc.visible_tabs))
                else:
                    print(f"[FAIL] Google Sheet accessible: {exc.code}")
    elif credentials_path:
        print("[BLOCKED] Google credentials file missing")
    else:
        print("[SKIP] GOOGLE_CREDENTIALS_FILE not configured")

    db_path = Path(os.environ.get("DATABASE_PATH", "data/smart_home.db"))
    if db_path.exists():
        try:
            conn = sqlite3.connect(db_path)
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
            conn.close()
            required = {"devices", "users", "command_log", "command_executions"}
            missing = sorted(required - tables)
            print(f"[{'FAIL' if missing else 'OK'}] DATABASE_PATH tables" + (f" missing={','.join(missing)}" if missing else ""))
        except sqlite3.Error:
            print("[FAIL] DATABASE_PATH unreadable")
    else:
        print("[FAIL] DATABASE_PATH missing")

    for path in ("data", "logs"):
        target = Path(path)
        target.mkdir(exist_ok=True)
        print(f"[OK] {path} directory")


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
