from __future__ import annotations

from smart_home_agent.agent_core.local_parser import parse_local
from smart_home_agent.services.device_resolver import resolve_device
from smart_home_agent.skills.base import BaseSkill, SkillContext, SkillResult, SkillRoute


class DeviceInfoSkill(BaseSkill):
    name = "home.device_info"
    supported_intents = ("device_info",)
    examples = ("đèn tròn là thiết bị nào", "thiết bị này ở phòng nào")
    required_role = "viewer"

    def match(self, context: SkillContext) -> SkillRoute | None:
        intent = parse_local(context.adapter_input.text)
        if intent and intent.intent == "device_info":
            return SkillRoute(
                intent=intent.intent,
                skill=self.name,
                confidence=intent.confidence,
                entities={"device_name": intent.device_name},
                parsed_intent=intent,
                requires_tool=True,
            )
        return None

    def execute(self, context: SkillContext, route: SkillRoute) -> SkillResult:
        intent = route.parsed_intent
        assert intent is not None
        device = resolve_device(context.conn, intent)
        reply = f"{device.canonical_name} là thiết bị loại {device.device_type} ở khu vực {device.room}."
        return SkillResult(
            reply=reply,
            status="device_info",
            parsed_intent=intent,
            resolved_device_ids=[device.device_id],
            resolved_entities=[ga for ga in _resolved_gas(device) if ga],
            resulting_state=reply,
        )


def _resolved_gas(device) -> list[str | None]:
    return [
        device.control_ga,
        device.status_ga,
        device.dim_control_ga,
        device.dim_status_ga,
        device.color_control_ga,
        device.color_status_ga,
    ]
