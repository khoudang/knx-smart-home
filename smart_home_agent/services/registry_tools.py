from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceSummary:
    device_id: str
    canonical_name: str
    room: str
    device_type: str
    notes: str | None

    @property
    def simulated(self) -> bool:
        return "SIMULATED" in (self.notes or "").upper()


def list_devices(conn: sqlite3.Connection, device_type: str | None = None) -> list[DeviceSummary]:
    query = """
        SELECT device_id, canonical_name, room, device_type, notes
        FROM devices
        WHERE enabled = 1 AND sync_status != 'deleted_from_sheet'
    """
    params: tuple[str, ...] = ()
    if device_type:
        query += " AND device_type = ?"
        params = (device_type,)
    query += " ORDER BY room, canonical_name"
    return [
        DeviceSummary(
            device_id=row["device_id"],
            canonical_name=row["canonical_name"],
            room=row["room"],
            device_type=row["device_type"],
            notes=row["notes"],
        )
        for row in conn.execute(query, params).fetchall()
    ]


def count_devices(conn: sqlite3.Connection, device_type: str | None = None) -> dict[str, int]:
    devices = list_devices(conn, device_type=device_type)
    simulated = sum(1 for device in devices if device.simulated)
    return {
        "total": len(devices),
        "simulated": simulated,
        "real": len(devices) - simulated,
    }


def count_devices_reply(conn: sqlite3.Connection, device_type: str | None = None) -> str:
    counts = count_devices(conn, device_type=device_type)
    noun = "đèn" if device_type == "light" else "thiết bị"
    if counts["simulated"]:
        return (
            f"Hiện hệ thống có {counts['total']} {noun}, "
            f"gồm {counts['real']} thiết bị KNX và {counts['simulated']} thiết bị mô phỏng."
        )
    return f"Hiện hệ thống có {counts['total']} {noun}."


def list_devices_reply(conn: sqlite3.Connection) -> str:
    devices = list_devices(conn)
    if not devices:
        return "Hiện chưa có thiết bị nào trong registry."
    names = ", ".join(device.canonical_name for device in devices)
    return f"Hiện có {len(devices)} thiết bị: {names}."


def get_device_status(conn: sqlite3.Connection, device_id: str) -> dict | None:
    row = conn.execute(
        "SELECT resulting_state FROM command_executions WHERE device_id = ? ORDER BY id DESC LIMIT 1",
        (device_id,),
    ).fetchone()
    if not row or not row["resulting_state"]:
        return None
    try:
        return json.loads(row["resulting_state"])
    except (TypeError, json.JSONDecodeError):
        return {"status": row["resulting_state"]}


def status_summary_reply(conn: sqlite3.Connection) -> str:
    devices = list_devices(conn)
    if not devices:
        return "Hiện chưa có thiết bị nào trong registry."
    return "Trạng thái hiện tại chưa được xác minh đầy đủ; hệ thống chỉ hiển thị trạng thái mô phỏng hoặc lần thực thi gần nhất."
