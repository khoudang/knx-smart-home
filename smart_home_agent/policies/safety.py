from __future__ import annotations

from dataclasses import dataclass

from smart_home_agent.agent_core.command_models import Intent
from smart_home_agent.services.device_resolver import DeviceMatch


SENSITIVE_TYPES = {"gate", "lock", "door_lock", "security"}


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    status: str
    reason: str | None = None
    requires_confirmation: bool = False


def evaluate_policy(intent: Intent, device: DeviceMatch | None, role: str, confirmed: bool = False) -> PolicyDecision:
    if device is None:
        return PolicyDecision(False, "validation_failed", "missing device")

    if role in {"blocked", "viewer"}:
        return PolicyDecision(False, "authorization_failed", f"{role} cannot execute device commands")

    sensitive = device.is_sensitive or device.device_type in SENSITIVE_TYPES
    if role in {"guest", "member"} and sensitive:
        return PolicyDecision(False, "authorization_failed", f"{role} cannot execute sensitive commands")

    if sensitive and intent.intent in {"turn_on", "run_scene"} and not confirmed:
        return PolicyDecision(False, "confirmation_required", "sensitive command requires confirmation", True)

    if device.requires_confirmation and not confirmed:
        return PolicyDecision(False, "confirmation_required", "device requires confirmation", True)

    return PolicyDecision(True, "allowed")
