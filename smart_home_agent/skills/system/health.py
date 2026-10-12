from __future__ import annotations

import os

from smart_home_agent.agent_core.command_models import Intent
from smart_home_agent.skills.base import BaseSkill, SkillContext, SkillResult, SkillRoute
from smart_home_agent.skills.common import normalize_text


class HealthSkill(BaseSkill):
    name = "system.health"
    supported_intents = ("system_health",)
    examples = ("hệ thống có ổn không", "KNX bridge đang dry-run hay live")
    required_role = "admin"

    def match(self, context: SkillContext) -> SkillRoute | None:
        normalized = normalize_text(context.adapter_input.text)
        if any(
            term in normalized
            for term in (
                "he thong co on khong",
                "he thong on khong",
                "bot co on khong",
                "bot dang chay on khong",
                "trang thai he thong",
                "kiem tra he thong",
                "knx bridge",
                "dry-run",
                "dry run",
                "knx dry run",
                "mqtt dry run",
                "doctor",
            )
        ):
            return SkillRoute(intent="system_health", skill=self.name, confidence=0.85, parsed_intent=Intent(intent="general_chat", confidence=0.85))
        return None

    def execute(self, context: SkillContext, route: SkillRoute) -> SkillResult:
        if context.user_role.role not in {"owner", "admin"}:
            return SkillResult(
                reply="Bạn không có quyền xem thông tin kỹ thuật hệ thống.",
                status="authorization_failed",
                success=False,
                error_code="permission_denied",
            )
        mqtt = os.environ.get("MQTT_DRY_RUN", "")
        knx = os.environ.get("KNX_DRY_RUN", "")
        reply = f"Hệ thống đang chạy với MQTT_DRY_RUN={mqtt}, KNX_DRY_RUN={knx}."
        return SkillResult(reply=reply, status="system_health", resulting_state=reply)
