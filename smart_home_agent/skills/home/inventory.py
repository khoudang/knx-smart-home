from __future__ import annotations

from smart_home_agent.agent_core.local_parser import parse_local
from smart_home_agent.services.registry_tools import count_devices_reply, list_devices_reply, status_summary_reply
from smart_home_agent.skills.base import BaseSkill, SkillContext, SkillResult, SkillRoute


class InventorySkill(BaseSkill):
    name = "home.inventory"
    supported_intents = ("count_devices", "list_devices", "status_summary")
    examples = ("nhà có bao nhiêu thiết bị", "danh sách thiết bị", "đèn nào đang bật")
    required_role = "viewer"

    def match(self, context: SkillContext) -> SkillRoute | None:
        intent = parse_local(context.adapter_input.text)
        if intent and intent.intent in self.supported_intents:
            return SkillRoute(
                intent=intent.intent,
                skill=self.name,
                confidence=intent.confidence,
                parsed_intent=intent,
                requires_tool=True,
            )
        return None

    def execute(self, context: SkillContext, route: SkillRoute) -> SkillResult:
        intent = route.parsed_intent
        assert intent is not None
        if intent.intent == "count_devices":
            reply = count_devices_reply(context.conn, device_type=intent.parameters.get("device_type"))
        elif intent.intent == "list_devices":
            reply = list_devices_reply(context.conn)
        else:
            reply = status_summary_reply(context.conn)
        return SkillResult(reply=reply, status=intent.intent, parsed_intent=intent, resulting_state=reply)
