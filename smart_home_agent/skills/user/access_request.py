from __future__ import annotations

from smart_home_agent.services.access_control import create_access_request, is_access_request_text
from smart_home_agent.skills.base import BaseSkill, SkillContext, SkillResult, SkillRoute


class AccessRequestSkill(BaseSkill):
    name = "user.access_request"
    supported_intents = ("access_request",)
    examples = ("xin quyền", "cho tôi dùng bot")
    required_role = "guest"

    def match(self, context: SkillContext) -> SkillRoute | None:
        if is_access_request_text(context.adapter_input.text):
            return SkillRoute(intent="access_request", skill=self.name, confidence=0.98, requires_tool=True)
        return None

    def execute(self, context: SkillContext, route: SkillRoute) -> SkillResult:
        result = create_access_request(
            context.conn,
            channel=context.adapter_input.channel,
            user_id=context.adapter_input.user_id,
            display_name=context.adapter_input.display_name,
            username=context.adapter_input.username,
            source_message_id=context.adapter_input.source_message_id,
        )
        status = f"access_request_{result.status}"
        return SkillResult(
            reply=result.reply,
            status=status,
            success=result.status in {"pending", "already_authorized"},
            resulting_state=result.reply,
            notifications=result.notifications,
        )
