from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any


SHEET_COLUMN_MAPPING = {
    "device_id": "device_id",
    "Device ID": "device_id",
    "ID": "device_id",
    "Ten line den": "canonical_name",
    "Tên line đèn": "canonical_name",
    "Ten thiet bi": "canonical_name",
    "Khu vuc": "room",
    "Khu vực": "room",
    "Phong": "room",
    "Bo dieu khien": "controller",
    "Bộ điều khiển": "controller",
    "Ma line den": "line_code",
    "Mã line đèn": "line_code",
    "Group Dali": "dali_group",
    "Dia chi dali": "dali_address",
    "Địa chỉ dali": "dali_address",
    "G.A Control": "control_ga",
    "G.A status": "status_ga",
    "G.A dim 1 byte value": "dim_control_ga",
    "G.A dim 1 byte value status": "dim_status_ga",
    "G.A color value": "color_control_ga",
    "G.A color value status": "color_status_ga",
    "Cb cap nguon": "power_circuit",
    "Cb cấp nguồn": "power_circuit",
    "Ghi chu": "notes",
    "Ghi chú": "notes",
    "device_type": "device_type",
    "aliases": "aliases",
    "capabilities": "capabilities",
    "homebridge_accessory_id": "homebridge_accessory_id",
    "enabled": "enabled",
    "is_sensitive": "is_sensitive",
    "requires_confirmation": "requires_confirmation",
}

GA_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{1,3}$")


@dataclass(frozen=True)
class SyncResult:
    inserted: int
    updated: int
    unchanged: int
    disabled: int
    invalid: int


def map_sheet_row(row: dict[str, Any]) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for source, target in SHEET_COLUMN_MAPPING.items():
        if source in row and row[source] not in (None, ""):
            mapped[target] = row[source]
    mapped.setdefault("device_type", "light")
    mapped.setdefault("capabilities", ["turn_on", "turn_off", "read_state"])
    if isinstance(mapped["capabilities"], str):
        mapped["capabilities"] = _list_value(mapped["capabilities"])
    mapped["capabilities"] = _capabilities_for_mapping(mapped)
    if "aliases" in mapped and isinstance(mapped["aliases"], str):
        mapped["aliases"] = _list_value(mapped["aliases"])
    return mapped


def validate_device(device: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in ("device_id", "canonical_name", "room", "device_type"):
        if not str(device.get(field, "")).strip():
            errors.append(f"missing {field}")
    for field in ("control_ga", "status_ga", "dim_control_ga", "dim_status_ga", "color_control_ga", "color_status_ga"):
        value = device.get(field)
        if value and not GA_RE.match(str(value).strip()):
            errors.append(f"invalid {field}")
    if not any(device.get(field) for field in ("control_ga", "dim_control_ga", "color_control_ga")):
        errors.append("missing control path")
    return errors


def sync_devices(conn: sqlite3.Connection, sheet_rows: list[dict[str, Any]]) -> SyncResult:
    sync_id = str(uuid.uuid4())
    mapped = [map_sheet_row(row) for row in sheet_rows if not _blank_row(row) and not _template_only_row(row) and not _scene_row(row)]
    errors = [validate_device(device) for device in mapped]
    invalid = sum(1 for item in errors if item)
    if invalid:
        with conn:
            conn.execute(
                """
                INSERT INTO sync_history
                    (sync_id, source, status, finished_at, invalid_count, error_message)
                VALUES (?, 'google_sheet', 'failed', CURRENT_TIMESTAMP, ?, ?)
                """,
                (sync_id, invalid, f"device sync validation failed: {invalid} invalid rows"),
            )
        raise ValueError(f"device sync validation failed: {invalid} invalid rows")

    ids = [device["device_id"] for device in mapped]
    if len(ids) != len(set(ids)):
        with conn:
            conn.execute(
                """
                INSERT INTO sync_history
                    (sync_id, source, status, finished_at, invalid_count, error_message)
                VALUES (?, 'google_sheet', 'failed', CURRENT_TIMESTAMP, ?, ?)
                """,
                (sync_id, len(ids) - len(set(ids)), "duplicate device_id in sheet"),
            )
        raise ValueError("duplicate device_id in sheet")
    inserted = updated = unchanged = disabled = 0
    with conn:
        conn.execute(
            "INSERT INTO sync_history (sync_id, source, status) VALUES (?, 'google_sheet', 'running')",
            (sync_id,),
        )
        existing = {
            row["device_id"]: dict(row)
            for row in conn.execute("SELECT * FROM devices").fetchall()
        }
        active_existing = {device_id for device_id, row in existing.items() if row.get("sync_status") == "active"}
        seen = set()
        for device in mapped:
            seen.add(device["device_id"])
            current = existing.get(device["device_id"])
            payload = _db_payload(device)
            if current is None:
                _insert_device(conn, payload)
                inserted += 1
            elif _same_device(current, payload):
                unchanged += 1
            else:
                _update_device(conn, payload)
                updated += 1
        for missing_id in active_existing - seen:
            conn.execute(
                "UPDATE devices SET enabled = 0, sync_status = 'deleted_from_sheet', local_updated_at = CURRENT_TIMESTAMP WHERE device_id = ?",
                (missing_id,),
            )
            disabled += 1
        conn.execute(
            """
            UPDATE sync_history
            SET status = 'success', finished_at = CURRENT_TIMESTAMP,
                inserted_count = ?, updated_count = ?, unchanged_count = ?, disabled_count = ?, invalid_count = 0
            WHERE sync_id = ?
            """,
            (inserted, updated, unchanged, disabled, sync_id),
        )
    return SyncResult(inserted, updated, unchanged, disabled, 0)


def _db_payload(device: dict[str, Any]) -> dict[str, Any]:
    return {
        "device_id": str(device["device_id"]).strip(),
        "canonical_name": str(device["canonical_name"]).strip(),
        "aliases": json.dumps(device.get("aliases", []), ensure_ascii=False),
        "controller": device.get("controller"),
        "line_code": device.get("line_code"),
        "room": str(device["room"]).strip(),
        "device_type": str(device["device_type"]).strip(),
        "dali_group": device.get("dali_group"),
        "dali_address": device.get("dali_address"),
        "control_ga": device.get("control_ga"),
        "status_ga": device.get("status_ga"),
        "dim_control_ga": device.get("dim_control_ga"),
        "dim_status_ga": device.get("dim_status_ga"),
        "color_control_ga": device.get("color_control_ga"),
        "color_status_ga": device.get("color_status_ga"),
        "homebridge_accessory_id": device.get("homebridge_accessory_id"),
        "power_circuit": device.get("power_circuit"),
        "notes": device.get("notes"),
        "capabilities": json.dumps(device.get("capabilities", []), ensure_ascii=False),
        "ha_entity_id": None,
        "enabled": int(str(device.get("enabled", "1")).lower() not in {"0", "false", "no"}),
        "is_sensitive": int(str(device.get("is_sensitive", "0")).lower() in {"1", "true", "yes", "on"}),
        "requires_confirmation": int(
            str(device.get("requires_confirmation", "0")).lower() in {"1", "true", "yes", "on"}
        ),
    }


def _insert_device(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    fields = list(payload)
    placeholders = ", ".join("?" for _ in fields)
    conn.execute(
        f"INSERT INTO devices ({', '.join(fields)}, local_updated_at) VALUES ({placeholders}, CURRENT_TIMESTAMP)",
        tuple(payload[field] for field in fields),
    )


def _update_device(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    fields = [field for field in payload if field != "device_id"]
    set_clause = ", ".join(f"{field} = ?" for field in fields)
    conn.execute(
        f"UPDATE devices SET {set_clause}, sync_status = 'active', local_updated_at = CURRENT_TIMESTAMP WHERE device_id = ?",
        tuple(payload[field] for field in fields) + (payload["device_id"],),
    )


def _same_device(current: dict[str, Any], payload: dict[str, Any]) -> bool:
    return all(str(current.get(key) or "") == str(value or "") for key, value in payload.items())


def _list_value(value: str) -> list[str]:
    stripped = value.strip()
    if stripped.startswith("["):
        parsed = json.loads(stripped)
        if not isinstance(parsed, list):
            raise ValueError("expected JSON array")
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in stripped.split(",") if item.strip()]


def _capabilities_for_mapping(device: dict[str, Any]) -> list[str]:
    capabilities = list(dict.fromkeys(device.get("capabilities", [])))
    if device.get("dim_control_ga") and "set_brightness" not in capabilities:
        capabilities.append("set_brightness")
    if device.get("color_control_ga") and "set_color" not in capabilities:
        capabilities.append("set_color")
    return capabilities


def _blank_row(row: dict[str, Any]) -> bool:
    return all(value is None or str(value).strip() == "" for value in row.values())


def _template_only_row(row: dict[str, Any]) -> bool:
    mapped = {target: row.get(source) for source, target in SHEET_COLUMN_MAPPING.items() if source in row}
    identity_fields = (
        "device_id",
        "canonical_name",
        "room",
        "control_ga",
        "dim_control_ga",
        "color_control_ga",
    )
    return not any(str(mapped.get(field) or "").strip() for field in identity_fields)


def _scene_row(row: dict[str, Any]) -> bool:
    values = {str(key).strip().lower(): str(value or "").strip().lower() for key, value in row.items()}
    device_type = values.get("device_type", "")
    name = values.get("ten line den") or values.get("tên line đèn") or values.get("TÃªn line Ä‘Ã¨n".lower(), "")
    device_id = values.get("device_id", "")
    return device_type == "scene" or name.startswith("scene") or device_id.startswith("scene")
