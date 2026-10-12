from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from smart_home_agent.agent_core.command_models import Intent
from smart_home_agent.skills.base import BaseSkill, SkillContext, SkillResult, SkillRoute
from smart_home_agent.skills.common import normalize_text


class TimeDateSkill(BaseSkill):
    name = "external.time_date"
    supported_intents = ("time_date",)
    examples = ("mấy giờ rồi", "hôm nay thứ mấy")
    required_role = "viewer"

    def match(self, context: SkillContext) -> SkillRoute | None:
        normalized = normalize_text(context.adapter_input.text)
        if any(
            term in normalized
            for term in (
                "may gio",
                "bay gio may gio",
                "gio may",
                "gio hien tai",
                "hom nay la ngay nao",
                "hom nay thu may",
                "hom nay la thu may",
                "ngay may",
                "hom nay ngay",
                "ngay hom nay",
            )
        ):
            return SkillRoute(
                intent="time_date",
                skill=self.name,
                confidence=0.9,
                parsed_intent=Intent(intent="general_chat", confidence=0.9),
            )
        return None

    def execute(self, context: SkillContext, route: SkillRoute) -> SkillResult:
        now = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
        reply = f"Bây giờ là {now:%H:%M}, ngày {now:%d/%m/%Y} theo giờ Việt Nam."
        return SkillResult(reply=reply, status="time_date", resulting_state=reply)
