from __future__ import annotations

import argparse
from pathlib import Path

from smart_home_agent.storage.sqlite import connect, migrate


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SQLite migrations.")
    parser.add_argument("--db", default="data/smart_home.db", help="SQLite database path")
    args = parser.parse_args()

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        migrate(conn)
        print(f"[OK] Migrated {db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
