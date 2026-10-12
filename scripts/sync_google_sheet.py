from __future__ import annotations

import argparse
import os
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
from smart_home_agent.services.device_sync import sync_devices
from smart_home_agent.storage.sqlite import connect, migrate


def main() -> None:
    load_env_file(Path(".env.production"))
    load_env_file(Path(".env"))
    expected_account = os.environ.get("GOOGLE_EXPECTED_SERVICE_ACCOUNT", EXPECTED_SERVICE_ACCOUNT)
    parser = argparse.ArgumentParser(description="Sync device registry from the Google Sheet devices tab.")
    parser.add_argument("--database", default=os.environ.get("DATABASE_PATH", "data/smart_home.db"))
    parser.add_argument("--sheet-id", default=os.environ.get("GOOGLE_SHEET_ID"))
    parser.add_argument("--tab", default=os.environ.get("GOOGLE_SHEET_TAB", "devices"))
    parser.add_argument("--credentials", default=None)
    args = parser.parse_args()

    args.sheet_id = args.sheet_id or os.environ.get("GOOGLE_SHEET_ID")
    args.tab = args.tab or os.environ.get("GOOGLE_SHEET_TAB", "devices")

    if not args.sheet_id:
        raise SystemExit("GOOGLE_SHEET_ID is required")

    credentials = Path(args.credentials) if args.credentials else google_credentials_path()
    if not credentials:
        raise SystemExit("GOOGLE_CREDENTIALS_FILE is required")
    if not credentials.is_absolute():
        credentials = Path.cwd() / credentials
    if not credentials.exists():
        raise SystemExit("missing_google_service_account_json")

    try:
        info = load_service_account_info(credentials)
    except Exception as exc:
        raise SystemExit(f"invalid_credentials: {type(exc).__name__}") from exc
    if not valid_service_account_info(info, expected_account):
        raise SystemExit("invalid_credentials")

    try:
        service = build_sheets_service(credentials)
        assert_tab_exists(service, args.sheet_id, args.tab)
        rows = fetch_tab_rows(service, args.sheet_id, args.tab)
    except GoogleSheetError as exc:
        if exc.code == "tab_not_found" and exc.visible_tabs:
            print("visible_tabs=" + ",".join(exc.visible_tabs))
        raise SystemExit(exc.code) from exc

    db_path = Path(args.database)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        migrate(conn)
        result = sync_devices(conn, rows)
    finally:
        conn.close()

    valid_devices = result.inserted + result.updated + result.unchanged
    print(f"valid_devices={valid_devices}")
    print(f"inserted={result.inserted}")
    print(f"updated={result.updated}")
    print(f"unchanged={result.unchanged}")
    print(f"disabled={result.disabled}")
    print(f"invalid={result.invalid}")
    device_count = conn_count(db_path)
    print(f"device_count={device_count}")


def conn_count(db_path: Path) -> int:
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) AS c FROM devices WHERE enabled = 1").fetchone()
        return int(row["c"])
    finally:
        conn.close()


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
