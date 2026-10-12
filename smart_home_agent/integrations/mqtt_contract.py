from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = 1
VALID_CAPABILITIES = {"power", "brightness", "color", "scene"}
COMMAND_ACTIONS = {"set", "activate"}
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
FORBIDDEN_COMMAND_FIELDS = {"ga", "group_address", "control_ga", "status_ga", "dim_control_ga", "color_control_ga"}


@dataclass(frozen=True)
class ParsedCommandTopic:
    device_id: str
    capability: str
    action: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def capability_for_intent(intent: str) -> str:
    if intent in {"turn_on", "turn_off"}:
        return "power"
    if intent == "set_brightness":
        return "brightness"
    if intent == "set_color":
        return "color"
    if intent == "run_scene":
        return "scene"
    raise ValueError(f"unsupported MQTT intent {intent}")


def command_topic(device_id: str, capability: str) -> str:
    validate_device_id(device_id)
    validate_capability(capability)
    action = "activate" if capability == "scene" else "set"
    return f"cmd/{device_id}/{capability}/{action}"


def state_topic(device_id: str, capability: str) -> str:
    validate_device_id(device_id)
    validate_capability(capability)
    return f"state/{device_id}/{capability}"


def result_topic(request_id: str) -> str:
    if not request_id:
        raise ValueError("request_id is required")
    return f"result/{request_id}"


def parse_command_topic(topic: str) -> ParsedCommandTopic:
    parts = topic.split("/")
    if len(parts) != 4 or parts[0] != "cmd":
        raise ValueError("invalid command topic")
    _, device_id, capability, action = parts
    validate_device_id(device_id)
    validate_capability(capability)
    if action not in COMMAND_ACTIONS:
        raise ValueError("invalid command action")
    if capability == "scene" and action != "activate":
        raise ValueError("scene commands must use activate")
    if capability != "scene" and action != "set":
        raise ValueError("device commands must use set")
    return ParsedCommandTopic(device_id, capability, action)


def build_command_payload(request_id: str, device_id: str, capability: str, value: Any) -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "device_id": device_id,
        "capability": capability,
        "value": value,
        "timestamp": now_iso(),
    }
    validate_command_payload(payload)
    return payload


def build_state_payload(device_id: str, capability: str, value: Any, source: str = "knx") -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "device_id": device_id,
        "capability": capability,
        "value": value,
        "source": source,
        "timestamp": now_iso(),
    }
    validate_state_payload(payload)
    return payload


def build_result_payload(request_id: str, status: str, error_code: str | None = None, **extra: Any) -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "status": status,
        "error_code": error_code,
        "timestamp": now_iso(),
    }
    for key, value in extra.items():
        if value is not None:
            payload[key] = value
    validate_result_payload(payload)
    return payload


def validate_command_payload(payload: dict[str, Any]) -> None:
    allowed = {"schema_version", "request_id", "device_id", "capability", "value", "timestamp"}
    _reject_unknown(payload, allowed)
    if FORBIDDEN_COMMAND_FIELDS.intersection(payload):
        raise ValueError("payload must not contain KNX group addresses")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    if not payload.get("request_id"):
        raise ValueError("request_id is required")
    validate_device_id(str(payload.get("device_id") or ""))
    capability = str(payload.get("capability") or "")
    validate_capability(capability)
    _validate_value(capability, payload.get("value"))
    if not payload.get("timestamp"):
        raise ValueError("timestamp is required")


def validate_state_payload(payload: dict[str, Any]) -> None:
    allowed = {"schema_version", "device_id", "capability", "value", "source", "timestamp"}
    _reject_unknown(payload, allowed)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    validate_device_id(str(payload.get("device_id") or ""))
    capability = str(payload.get("capability") or "")
    validate_capability(capability)
    _validate_value(capability, payload.get("value"))
    if payload.get("source") not in {"knx", "simulator", "test"}:
        raise ValueError("invalid state source")
    if not payload.get("timestamp"):
        raise ValueError("timestamp is required")


def validate_result_payload(payload: dict[str, Any]) -> None:
    allowed = {
        "schema_version",
        "request_id",
        "status",
        "error_code",
        "timestamp",
        "resolved_device_id",
        "resolved_capability",
    }
    _reject_unknown(payload, allowed)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    if not payload.get("request_id"):
        raise ValueError("request_id is required")
    if not payload.get("status"):
        raise ValueError("status is required")


def validate_device_id(device_id: str) -> None:
    if not DEVICE_ID_RE.match(device_id):
        raise ValueError("invalid device_id")


def validate_capability(capability: str) -> None:
    if capability not in VALID_CAPABILITIES:
        raise ValueError("invalid capability")


def _validate_value(capability: str, value: Any) -> None:
    if capability == "power" and not isinstance(value, bool):
        raise ValueError("power value must be boolean")
    if capability == "brightness" and not (isinstance(value, int) and 0 <= value <= 100):
        raise ValueError("brightness value must be integer 0..100")
    if capability == "color":
        if isinstance(value, int) and 1000 <= value <= 10000:
            return
        if isinstance(value, dict) and value:
            return
        raise ValueError("color value must be color temperature or object")
    if capability == "scene" and value != "activate":
        raise ValueError("scene value must be activate")


def _reject_unknown(payload: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"unknown payload field: {sorted(unknown)[0]}")
