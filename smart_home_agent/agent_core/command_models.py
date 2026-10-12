from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SUPPORTED_INTENTS = {
    "turn_on",
    "turn_off",
    "set_brightness",
    "set_color",
    "read_state",
    "count_devices",
    "list_devices",
    "status_summary",
    "device_info",
    "greeting",
    "run_scene",
    "create_scene",
    "list_scenes",
    "weather",
    "web_search",
    "general_chat",
    "clarification",
}


@dataclass(frozen=True)
class Intent:
    intent: str
    device_name: str | None = None
    room: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    scene_name: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Intent":
        intent = str(data.get("intent", "")).strip()
        if intent not in SUPPORTED_INTENTS:
            raise ValueError(f"unsupported intent: {intent}")
        confidence = float(data.get("confidence", 0.0))
        if confidence < 0 or confidence > 1:
            raise ValueError("confidence must be between 0 and 1")
        parameters = data.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise ValueError("parameters must be an object")
        return cls(
            intent=intent,
            device_name=_optional_str(data.get("device_name")),
            room=_optional_str(data.get("room")),
            parameters=parameters,
            confidence=confidence,
            scene_name=_optional_str(data.get("scene_name")),
        )


@dataclass(frozen=True)
class AdapterInput:
    request_id: str
    source_message_id: str
    channel: str
    user_id: str
    text: str
    role: str = "guest"
    display_name: str | None = None
    username: str | None = None

    def __post_init__(self) -> None:
        for name in ("request_id", "source_message_id", "channel", "user_id", "text"):
            if not getattr(self, name):
                raise ValueError(f"{name} is required")


@dataclass(frozen=True)
class ResolvedCommand:
    intent: Intent
    device_id: str | None
    control_ga: str | None
    sensitive: bool = False
    requires_confirmation: bool = False


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
