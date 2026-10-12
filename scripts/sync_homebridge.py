from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("/var/lib/homebridge/config.json")
DEFAULT_MANIFEST = PROJECT_ROOT / "data/homebridge-generated-manifest.json"
DEFAULT_STATE = PROJECT_ROOT / "data/homebridge-sync-state.json"
BACKUP_DIR = PROJECT_ROOT / "data/backups"
GENERATOR = "smart-home-agent"


@dataclass(frozen=True)
class Plan:
    add: list[str]
    update: list[str]
    remove: list[str]
    unchanged: list[str]
    unsupported: list[str]
    errors: list[str]
    changed: bool
    desired_hash: str
    desired_accessories: list[dict[str, Any]]
    managed_current_indexes: dict[str, int]


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile SQLite device registry into Homebridge mqttthing config.")
    parser.add_argument("--database", default=os.environ.get("DATABASE_PATH", "data/smart_home.db"))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--dry-run", action="store_true", help="Only print the plan. This is the default.")
    parser.add_argument("--apply", action="store_true", help="Apply config changes and restart Homebridge if needed.")
    parser.add_argument("--homebridge-unit", default="homebridge")
    args = parser.parse_args()

    if args.apply and args.dry_run:
        raise SystemExit("--apply and --dry-run are mutually exclusive")

    config_path = Path(args.config)
    manifest_path = Path(args.manifest)
    conn = sqlite3.connect(args.database)
    conn.row_factory = sqlite3.Row
    try:
        config = load_config(config_path)
        devices = load_homebridge_devices(conn)
        plan = build_plan(config, devices, manifest_path)
    finally:
        conn.close()

    print_plan(plan)
    if plan.errors:
        raise SystemExit("homebridge_plan_has_errors")
    if not args.apply:
        print("mode=dry_run")
        return
    if not plan.changed:
        if manifest_path.exists():
            make_project_readable(manifest_path)
        write_state(DEFAULT_STATE, plan.desired_hash)
        print("mode=apply")
        print("changed=false")
        print("homebridge_restart=false")
        return

    backup_path = backup_config(config_path)
    print(f"backup_path={backup_path}")
    new_config = reconcile_config(config, plan.desired_accessories, plan.managed_current_indexes)
    try:
        atomic_write_json(config_path, new_config)
        restart_homebridge(args.homebridge_unit)
        assert_homebridge_healthy(args.homebridge_unit)
    except Exception as exc:
        restore_config(backup_path, config_path)
        restart_homebridge(args.homebridge_unit)
        raise SystemExit(f"homebridge_apply_failed_rollback_complete: {type(exc).__name__}") from exc
    write_manifest(manifest_path, plan.desired_accessories, plan.desired_hash)
    write_state(DEFAULT_STATE, plan.desired_hash)
    prune_backups(BACKUP_DIR)
    print("mode=apply")
    print("changed=true")
    print("homebridge_restart=true")


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Homebridge config must be a JSON object")
    data.setdefault("accessories", [])
    return data


def load_homebridge_devices(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM devices
        WHERE enabled = 1
          AND sync_status != 'deleted_from_sheet'
          AND (
            COALESCE(homebridge_accessory_id, '') != ''
            OR device_id LIKE 'sim_%'
          )
        ORDER BY device_id
        """
    ).fetchall()


def build_plan(config: dict[str, Any], devices: list[sqlite3.Row], manifest_path: Path) -> Plan:
    current = list(config.get("accessories") or [])
    desired: list[dict[str, Any]] = []
    add: list[str] = []
    update: list[str] = []
    unchanged: list[str] = []
    unsupported: list[str] = []
    errors: list[str] = []
    current_by_device = infer_managed_current(current, devices, manifest_path)

    seen_accessory_ids: set[str] = set()
    duplicate_device_ids: set[str] = set()
    for row in devices:
        built = build_accessory(row)
        if built is None:
            unsupported.append(row["device_id"])
            continue
        accessory_id, accessory = built
        if accessory_id in seen_accessory_ids:
            errors.append(f"duplicate accessory identity: {accessory_id}")
            duplicate_device_ids.add(row["device_id"])
            continue
        seen_accessory_ids.add(accessory_id)
        desired.append(accessory)
        current_index = current_by_device.get(row["device_id"])
        if current_index is None:
            add.append(row["device_id"])
        elif normalized_accessory(current[current_index]) == normalized_accessory(accessory):
            unchanged.append(row["device_id"])
        else:
            update.append(row["device_id"])

    desired_ids = {acc["_manifest"]["device_id"] for acc in desired}
    remove = sorted(device_id for device_id in current_by_device if device_id not in desired_ids and device_id not in duplicate_device_ids)
    desired_hash = hash_json([normalized_accessory(acc) for acc in desired])
    changed = bool(add or update or remove)
    return Plan(add, update, remove, unchanged, unsupported, errors, changed, desired_hash, desired, current_by_device)


def build_accessory(row: sqlite3.Row) -> tuple[str, dict[str, Any]] | None:
    device_id = row["device_id"]
    accessory_id = row["homebridge_accessory_id"] or device_id
    capabilities = set(json.loads(row["capabilities"] or "[]"))
    device_type = row["device_type"]
    room_slug = homebridge_room_slug(row)
    base = f"{room_slug}/{accessory_id}"
    name = row["canonical_name"]

    if device_type == "light":
        topics = light_topics(row, base)
        if "set_brightness" in capabilities:
            topics.update(brightness_topics(row, base))
        accessory = mqttthing(name, "lightbulb", topics)
    elif device_type == "curtain":
        accessory = mqttthing(
            name,
            "windowCovering",
            {
                "getCurrentPosition": f"state/{base}/position",
                "getTargetPosition": f"cmd/{base}/position",
                "getPositionState": f"state/{base}/positionState",
            },
        )
    elif device_type in {"ac", "air_conditioner"}:
        accessory = mqttthing(
            name,
            "thermostat",
            {
                "getOn": f"state/{base}/power",
                "setOn": f"cmd/{base}/power",
                "getCurrentHeatingCoolingState": f"state/{base}/power",
                "getTargetHeatingCoolingState": f"cmd/{base}/power",
                "getCurrentTemperature": f"state/{base}/temperature",
                "getTargetTemperature": f"state/{base}/temperature",
                "setTargetTemperature": f"cmd/{base}/temperature",
            },
        )
    elif device_type == "sensor":
        if "motion" in capabilities or "motion" in accessory_id:
            accessory = mqttthing(name, "motionSensor", {"getMotionDetected": f"state/{base}/motion"})
        elif "hum" in accessory_id or "humidity" in capabilities:
            accessory = mqttthing(
                name,
                "weatherStation",
                {
                    "getCurrentTemperature": f"state/{base}/temperature",
                    "getCurrentRelativeHumidity": f"state/{base}/humidity",
                },
                {
                    "serviceNames": {
                        "temperature": f"{name} Temperature",
                        "humidity": f"{name} Humidity",
                    }
                },
            )
        else:
            accessory = mqttthing(name, "temperatureSensor", {"getCurrentTemperature": f"state/{base}/temperature"})
    elif device_type == "gate":
        accessory = mqttthing(name, "switch", {"getOn": f"state/{base}/power", "setOn": f"cmd/{base}/power"})
    elif device_type == "lock":
        accessory = mqttthing(
            name,
            "lockMechanism",
            {"getLockCurrentState": f"state/{base}/locked", "setLockTargetState": f"cmd/{base}/locked"},
        )
    else:
        return None

    accessory["_manifest"] = {
        "device_id": device_id,
        "homebridge_accessory_id": accessory_id,
        "area": row["room"],
        "generated_by": GENERATOR,
    }
    return accessory_id, accessory


def mqttthing(
    name: str, service_type: str, topics: dict[str, str], extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    accessory = {"accessory": "mqttthing", "type": service_type, "name": name, "topics": topics}
    if extra:
        accessory.update(extra)
    return accessory


def light_topics(row: sqlite3.Row, legacy_base: str) -> dict[str, str]:
    if is_simulated(row):
        return {"getOn": f"state/{legacy_base}/power", "setOn": f"cmd/{legacy_base}/power"}
    device_id = row["device_id"]
    return {"getOn": f"state/{device_id}/power", "setOn": f"cmd/{device_id}/power/set"}


def brightness_topics(row: sqlite3.Row, legacy_base: str) -> dict[str, str]:
    if is_simulated(row):
        return {"getBrightness": f"state/{legacy_base}/brightness", "setBrightness": f"cmd/{legacy_base}/brightness"}
    device_id = row["device_id"]
    return {"getBrightness": f"state/{device_id}/brightness", "setBrightness": f"cmd/{device_id}/brightness/set"}


def is_simulated(row: sqlite3.Row) -> bool:
    return str(row["device_id"]).startswith("sim_")


def homebridge_room_slug(row: sqlite3.Row) -> str:
    accessory_id = row["homebridge_accessory_id"] or row["device_id"]
    if accessory_id.startswith("lr_"):
        return "living_room"
    if accessory_id.startswith("br_"):
        return "bedroom"
    if accessory_id.startswith("gate_"):
        return "gate"
    return slug(row["room"] or "home")


def slug(value: str) -> str:
    text = value.strip().lower()
    replacements = {
        "phòng khách": "living_room",
        "phòng ngủ": "bedroom",
        "cổng": "gate",
    }
    if text in replacements:
        return replacements[text]
    safe = "".join(ch if ch.isalnum() else "_" for ch in text)
    return "_".join(part for part in safe.split("_") if part)


def infer_managed_current(current: list[dict[str, Any]], devices: list[sqlite3.Row], manifest_path: Path) -> dict[str, int]:
    by_device: dict[str, int] = {}
    manifest = load_manifest(manifest_path)
    for item in manifest.get("accessories", []):
        device_id = item.get("device_id")
        accessory_id = item.get("homebridge_accessory_id")
        if device_id and accessory_id:
            index = find_current_by_accessory_id(current, accessory_id)
            if index is None:
                index = find_current_by_accessory_id(current, device_id)
            if index is not None:
                by_device[device_id] = index
    for row in devices:
        if row["device_id"] in by_device:
            continue
        accessory_id = row["homebridge_accessory_id"] or row["device_id"]
        index = find_current_by_accessory_id(current, accessory_id)
        if index is None:
            index = find_current_by_accessory_id(current, row["device_id"])
        if index is not None:
            by_device[row["device_id"]] = index
    return by_device


def find_current_by_accessory_id(current: list[dict[str, Any]], accessory_id: str) -> int | None:
    needle = f"/{accessory_id}/"
    for idx, accessory in enumerate(current):
        topics = accessory.get("topics") or {}
        if any(needle in str(topic) for topic in topics.values()):
            return idx
    return None


def normalized_accessory(accessory: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in accessory.items() if key != "_manifest"}


def reconcile_config(config: dict[str, Any], desired: list[dict[str, Any]], managed_current_indexes: dict[str, int]) -> dict[str, Any]:
    new_config = json.loads(json.dumps(config, ensure_ascii=False))
    current = list(new_config.get("accessories") or [])
    remove_indexes = set(managed_current_indexes.values())
    kept = [acc for idx, acc in enumerate(current) if idx not in remove_indexes]
    generated = [normalized_accessory(acc) for acc in desired]
    new_config["accessories"] = kept + generated
    return new_config


def print_plan(plan: Plan) -> None:
    for label, values in (
        ("ADD", plan.add),
        ("UPDATE", plan.update),
        ("REMOVE", plan.remove),
        ("UNCHANGED", plan.unchanged),
        ("UNSUPPORTED", plan.unsupported),
        ("ERROR", plan.errors),
    ):
        print(f"{label}={len(values)}")
        for value in values:
            print(f"{label} accessory: {value}")
    print(f"desired_hash={plan.desired_hash}")
    print(f"changed={str(plan.changed).lower()}")


def backup_config(config_path: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = BACKUP_DIR / f"homebridge-config-{timestamp}.json"
    shutil.copy2(config_path, backup)
    return backup


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    json.loads(text)
    current_stat = path.stat() if path.exists() else None
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    if current_stat is not None:
        os.chown(tmp_path, current_stat.st_uid, current_stat.st_gid)
        os.chmod(tmp_path, current_stat.st_mode & 0o777)
    tmp_path.replace(path)


def restore_config(backup_path: Path, config_path: Path) -> None:
    shutil.copy2(backup_path, config_path)


def restart_homebridge(unit: str) -> None:
    subprocess.run(["systemctl", "restart", unit], check=True)


def assert_homebridge_healthy(unit: str) -> None:
    subprocess.run(["systemctl", "is-active", "--quiet", unit], check=True)
    logs = subprocess.run(["journalctl", "-u", unit, "-n", "80", "--no-pager"], check=False, text=True, capture_output=True)
    fatal = ("error loading", "config error", "eaddrinuse", "uncaught exception")
    if any(marker in logs.stdout.lower() for marker in fatal):
        raise RuntimeError("homebridge log contains fatal marker")


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"accessories": []}
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(path: Path, desired: list[dict[str, Any]], desired_hash: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_by": GENERATOR,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "desired_hash": desired_hash,
        "accessories": [accessory["_manifest"] | {"config_hash": hash_json(normalized_accessory(accessory))} for accessory in desired],
    }
    atomic_write_json(path, manifest)
    make_project_readable(path)


def write_state(path: Path, desired_hash: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {"desired_hash": desired_hash, "updated_at": datetime.now(timezone.utc).isoformat()})
    make_project_readable(path)


def make_project_readable(path: Path) -> None:
    project_stat = PROJECT_ROOT.stat()
    os.chown(path, project_stat.st_uid, project_stat.st_gid)
    os.chmod(path, 0o664)


def prune_backups(path: Path, keep: int = 10) -> None:
    backups = sorted(path.glob("homebridge-config-*.json"), reverse=True)
    for old in backups[keep:]:
        old.unlink(missing_ok=True)


def hash_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    main()
