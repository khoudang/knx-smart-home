from __future__ import annotations

from smart_home_agent.agent_core.command_models import Intent
from smart_home_agent.skills.base import BaseSkill, SkillContext, SkillResult, SkillRoute
from smart_home_agent.skills.common import (
    clarification_reply,
    is_short_negative_or_insult,
    looks_like_home_automation,
)


class GeneralChatSkill(BaseSkill):
    name = "external.general_chat"
    supported_intents = ("general_chat",)
    examples = ("Em ăn cơm chưa", "Tôi đói quá", "kể chuyện")
    required_role = "guest"

    def match(self, context: SkillContext) -> SkillRoute | None:
        if looks_like_home_automation(context.conn, context.adapter_input.text):
            return None
        return SkillRoute(
            intent="general_chat",
            skill=self.name,
            confidence=0.7,
            parsed_intent=Intent(intent="general_chat", confidence=0.7),
            requires_tool=True,
        )

    def execute(self, context: SkillContext, route: SkillRoute) -> SkillResult:
        if is_short_negative_or_insult(context.adapter_input.text):
            reply = "Mình hiểu bạn đang không hài lòng. Bạn nói rõ phần nào đang sai để mình hỗ trợ nhé."
        else:
            try:
                reply = context.intent_parser.chat(context.adapter_input.text) or clarification_reply()
            except Exception:
                reply = clarification_reply()
        return SkillResult(
            reply=reply,
            status="general_chat",
            parsed_intent=route.parsed_intent,
            resulting_state=reply,
        )
