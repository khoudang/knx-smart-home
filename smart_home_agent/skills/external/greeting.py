from __future__ import annotations

from smart_home_agent.agent_core.local_parser import parse_local
from smart_home_agent.skills.base import BaseSkill, SkillContext, SkillResult, SkillRoute


class GreetingSkill(BaseSkill):
    name = "external.greeting"
    supported_intents = ("greeting",)
    examples = ("xin chào", "hello", "bot ơi")
    required_role = "guest"

    def match(self, context: SkillContext) -> SkillRoute | None:
        intent = parse_local(context.adapter_input.text)
        if intent and intent.intent == "greeting":
            return SkillRoute(intent="greeting", skill=self.name, confidence=intent.confidence, parsed_intent=intent)
        return None

    def execute(self, context: SkillContext, route: SkillRoute) -> SkillResult:
        reply = "Xin chào. Mình đã sẵn sàng hỗ trợ kiểm tra và điều khiển nhà thông minh."
        return SkillResult(reply=reply, status="greeting", parsed_intent=route.parsed_intent, resulting_state=reply)
