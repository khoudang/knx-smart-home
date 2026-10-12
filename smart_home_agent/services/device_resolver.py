from __future__ import annotations

import json
import sqlite3
import unicodedata
from dataclasses import dataclass

from smart_home_agent.agent_core.command_models import Intent


@dataclass(frozen=True)
class DeviceMatch:
    device_id: str
    canonical_name: str
    aliases: list[str]
    room: str
    device_type: str
    capabilities: list[str]
    control_ga: str | None
    status_ga: str | None
    dim_control_ga: str | None
    dim_status_ga: str | None
    color_control_ga: str | None
    color_status_ga: str | None
    is_sensitive: bool
    requires_confirmation: bool


class DeviceResolutionError(Exception):
    code = "resolution_error"


class DeviceNotFound(DeviceResolutionError):
    code = "device_not_found"


class NoDevicesConfigured(DeviceNotFound):
    code = "no_devices_configured"


class AmbiguousDevice(DeviceResolutionError):
    code = "ambiguous_target"

    def __init__(self, matches: list[DeviceMatch]) -> None:
        self.matches = matches
        super().__init__("ambiguous device target")


class DisabledDevice(DeviceResolutionError):
    code = "device_disabled"


class CapabilityMismatch(DeviceResolutionError):
    code = "capability_mismatch"


INTENT_CAPABILITY = {
    "turn_on": "turn_on",
    "turn_off": "turn_off",
    "set_brightness": "set_brightness",
    "set_color": "set_color",
    "read_state": "read_state",
}


def resolve_device(conn: sqlite3.Connection, intent: Intent) -> DeviceMatch:
    if not intent.device_name:
        raise DeviceNotFound("missing device name")

    rows = conn.execute(
        """
        SELECT device_id, canonical_name, aliases, room, device_type, capabilities,
               control_ga, status_ga, dim_control_ga, dim_status_ga, color_control_ga,
               color_status_ga, enabled, is_sensitive, requires_confirmation
        FROM devices
        WHERE sync_status != 'deleted_from_sheet'
        """
    ).fetchall()

    candidates = [_row_to_match(row) for row in rows]
    if not candidates:
        raise NoDevicesConfigured("Chưa có thiết bị nào được cấu hình trong registry.")

    text = _norm(intent.device_name)
    room = _norm(intent.room)

    matches = _exact_matches(candidates, text)
    if not matches:
        matches = _contains_matches(candidates, text)
    if room:
        room_matches = [dev for dev in matches if _norm(dev.room) == room]
        if room_matches:
            matches = room_matches

    if not matches:
        raise DeviceNotFound(f"device not found: {intent.device_name}")
    if len(matches) > 1:
        raise AmbiguousDevice(matches)

    match = matches[0]
    enabled = conn.execute("SELECT enabled FROM devices WHERE device_id = ?", (match.device_id,)).fetchone()["enabled"]
    if not enabled:
        raise DisabledDevice(f"device disabled: {match.device_id}")

    required = INTENT_CAPABILITY.get(intent.intent)
    if required and required not in match.capabilities:
        raise CapabilityMismatch(f"{match.device_id} does not support {required}")
    return match


def _row_to_match(row: sqlite3.Row) -> DeviceMatch:
    capabilities = json.loads(row["capabilities"] or "[]")
    aliases = json.loads(row["aliases"] or "[]")
    return DeviceMatch(
        device_id=row["device_id"],
        canonical_name=row["canonical_name"],
        aliases=aliases,
        room=row["room"],
        device_type=row["device_type"],
        capabilities=capabilities,
        control_ga=row["control_ga"],
        status_ga=row["status_ga"],
        dim_control_ga=row["dim_control_ga"],
        dim_status_ga=row["dim_status_ga"],
        color_control_ga=row["color_control_ga"],
        color_status_ga=row["color_status_ga"],
        is_sensitive=bool(row["is_sensitive"]),
        requires_confirmation=bool(row["requires_confirmation"]),
    )


def _norm(value: str | None) -> str:
    compact = " ".join(_repair_mojibake(value or "").strip().lower().split())
    decomposed = unicodedata.normalize("NFKD", compact)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.replace("đ", "d").replace("Đ", "D")


def _repair_mojibake(value: str) -> str:
    try:
        repaired = value.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return repaired if "�" not in repaired else value


def _names(dev: DeviceMatch) -> list[str]:
    return [dev.canonical_name, *dev.aliases]


def _exact_matches(candidates: list[DeviceMatch], text: str) -> list[DeviceMatch]:
    return [dev for dev in candidates if text in [_norm(name) for name in _names(dev)]]


def _contains_matches(candidates: list[DeviceMatch], text: str) -> list[DeviceMatch]:
    return [dev for dev in candidates if any(text in _norm(name) for name in _names(dev))]
