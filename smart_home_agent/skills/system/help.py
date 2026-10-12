from __future__ import annotations

from smart_home_agent.agent_core.command_models import Intent
from smart_home_agent.skills.base import BaseSkill, SkillContext, SkillResult, SkillRoute
from smart_home_agent.skills.common import normalize_text


class HelpSkill(BaseSkill):
    name = "system.help"
    supported_intents = ("help", "identity")
    examples = ("bot làm được gì", "danh sách skill", "bạn tên gì")
    required_role = "guest"

    def match(self, context: SkillContext) -> SkillRoute | None:
        normalized = normalize_text(context.adapter_input.text)
        if normalized in {
            "ten la gi",
            "ban ten gi",
            "ban ten la gi",
            "bot ten gi",
            "bot ten la gi",
            "ten cua ban la gi",
            "ban la ai",
            "bot la ai",
            "day la bot gi",
            "aicontrol la gi",
        }:
            return SkillRoute(
                intent="identity",
                skill=self.name,
                confidence=0.95,
                parsed_intent=Intent(intent="general_chat", confidence=0.95),
            )
        if any(
            term in normalized
            for term in (
                "bot lam duoc gi",
                "ban lam duoc gi",
                "con bot nay lam duoc gi",
                "bot nay lam duoc gi",
                "huong dan",
                "huong dan su dung",
                "danh sach skill",
                "co nhung skill gi",
                "co the lam gi",
                "tro giup",
            )
        ):
            return SkillRoute(
                intent="help",
                skill=self.name,
                confidence=0.9,
                parsed_intent=Intent(intent="general_chat", confidence=0.9),
            )
        return None

    def execute(self, context: SkillContext, route: SkillRoute) -> SkillResult:
        if route.intent == "identity":
            reply = "Mình là AICONTROL, trợ lý nhà thông minh của bạn."
            return SkillResult(reply=reply, status="identity", resulting_state=reply)

        role = context.user_role.role
        basics = [
            "xem số lượng hoặc danh sách thiết bị",
            "hỏi thông tin thiết bị",
            "trò chuyện thông thường",
            "hỏi thời gian và thời tiết",
        ]
        if role in {"owner", "admin", "member"}:
            basics.append("điều khiển thiết bị theo cấu hình dry-run/live")
        if role in {"owner", "admin"}:
            basics.append("quản lý yêu cầu xin quyền và thành viên")
        reply = "Mình có thể: " + "; ".join(basics) + "."
        return SkillResult(reply=reply, status="help", resulting_state=reply)
