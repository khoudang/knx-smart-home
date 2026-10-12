from __future__ import annotations

import argparse
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Google Sheet sync, then plan or apply Homebridge reconciliation.")
    parser.add_argument("--database", default="data/smart_home.db")
    parser.add_argument("--apply-homebridge", action="store_true")
    args = parser.parse_args()

    run([sys.executable, "scripts/sync_devices.py", "--database", args.database])
    homebridge_mode = "--apply" if args.apply_homebridge else "--dry-run"
    run([sys.executable, "scripts/sync_homebridge.py", "--database", args.database, homebridge_mode])


def run(command: list[str]) -> None:
    print("running=" + " ".join(command))
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
