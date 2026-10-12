from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


DOMAINS = {
    "access_control",
    "device_control",
    "scene",
    "weather",
    "home_status",
    "web_search",
    "general_chat",
    "unknown",
}

INTENTS_BY_DOMAIN = {
    "access_control": {
        "request_access",
        "approve_access",
        "reject_access",
        "grant_permission",
        "revoke_permission",
        "revoke_all_permissions",
        "block_user",
        "unblock_user",
        "remove_user",
        "list_users",
        "list_blocked_users",
        "list_pending_requests",
        "check_user_permission",
    },
    "device_control": {
        "set_device_state",
        "set_device_value",
        "query_device_state",
        "query_room_state",
        "query_home_state",
    },
    "scene": {"activate_scene", "deactivate_scene", "list_scenes", "query_scene", "run_scene", "create_scene"},
    "weather": {"get_current_weather", "get_weather_forecast", "weather_query"},
    "home_status": {"health_check", "status_summary"},
    "web_search": {"search_information", "web_search"},
    "general_chat": {"chat"},
    "unknown": {"unknown"},
}


@dataclass(frozen=True)
class IntentClassification:
    domain: str
    intent: str
    entities: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    needs_clarification: bool = False
    clarification_question: str | None = None


class IntentSchemaError(ValueError):
    pass


def validate_classification_payload(payload: dict[str, Any]) -> IntentClassification:
    if not isinstance(payload, dict):
        raise IntentSchemaError("classification payload must be an object")

    domain = _required_str(payload, "domain")
    intent = _required_str(payload, "intent")
    if domain not in DOMAINS:
        raise IntentSchemaError(f"unsupported domain: {domain}")
    if intent not in INTENTS_BY_DOMAIN[domain]:
        raise IntentSchemaError(f"unsupported intent for {domain}: {intent}")

    entities = payload.get("entities") or {}
    if not isinstance(entities, dict):
        raise IntentSchemaError("entities must be an object")

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError) as exc:
        raise IntentSchemaError("confidence must be a number") from exc
    if confidence < 0 or confidence > 1:
        raise IntentSchemaError("confidence must be between 0 and 1")

    needs_clarification = bool(payload.get("needs_clarification", False))
    clarification_question = _optional_str(payload.get("clarification_question"))
    if needs_clarification and not clarification_question:
        clarification_question = "Bạn muốn mình xử lý việc gì?"

    return IntentClassification(
        domain=domain,
        intent=intent,
        entities=entities,
        confidence=confidence,
        needs_clarification=needs_clarification,
        clarification_question=clarification_question,
    )


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = _optional_str(payload.get(key))
    if not value:
        raise IntentSchemaError(f"{key} is required")
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
