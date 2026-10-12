from __future__ import annotations

from smart_home_agent.services.access_control import (
    AdminCommand,
    execute_admin_command,
    is_admin_domain_text,
    is_admin_write_action,
    is_access_request_text,
    normalized_text,
    parse_admin_command,
)
from smart_home_agent.skills.base import BaseSkill, SkillContext, SkillResult, SkillRoute
from smart_home_agent.skills.context import get_active_context, save_context


USER_LIST_FOLLOW_UPS = {
    "la ai",
    "ten cua ho la gi",
    "ten ho la gi",
    "ho ten gi",
    "ho la ai",
    "liet ke ho",
    "cho xem danh sach",
    "xem danh sach",
    "danh sach ho",
    "danh sach nhung nguoi do",
}

CONTEXTUAL_REVOKE_PREFIXES = ("xoa ", "go ", "tuoc ", "chan ")


class PermissionsSkill(BaseSkill):
    name = "user.permissions"
    supported_intents = ("admin_command",)
    examples = ("danh sách yêu cầu", "đồng ý", "từ chối", "duyệt 1 member")
    required_role = "admin"

    def match(self, context: SkillContext) -> SkillRoute | None:
        if is_access_request_text(context.adapter_input.text):
            return None

        follow_up = self._match_user_list_follow_up(context)
        if follow_up:
            return follow_up

        contextual_revoke = self._match_contextual_revoke(context)
        if contextual_revoke:
            return contextual_revoke

        command = parse_admin_command(context.adapter_input.text)
        if not command:
            if is_admin_domain_text(context.adapter_input.text):
                return SkillRoute(
                    intent="admin_command",
                    skill=self.name,
                    confidence=0.92,
                    entities={"action": "admin_clarification"},
                    requires_tool=False,
                    permission_action="admin_clarification",
                    clarification_needed=False,
                )
            return None
        return SkillRoute(
            intent="admin_command",
            skill=self.name,
            confidence=0.99,
            entities={"action": command.action},
            requires_tool=True,
            permission_action=command.action,
        )

    def execute(self, context: SkillContext, route: SkillRoute) -> SkillResult:
        if route.entities.get("action") == "admin_clarification":
            reply = (
                "Mình hiểu đây là yêu cầu về quyền/người dùng, nhưng chưa rõ bạn muốn làm gì. "
                "Bạn có thể nói: ai đang có quyền, danh sách yêu cầu, duyệt, từ chối, hoặc xóa quyền <tên>."
            )
            return SkillResult(
                reply=reply,
                status="admin_clarification",
                success=False,
                resulting_state=reply,
                error_code="admin_intent_unclear",
                error_message="admin domain matched but action is unclear",
            )
        command = self._command_from_route(context, route)
        if context.block_legacy_admin_writes and is_admin_write_action(command.action):
            reply = (
                "Mình cần xác minh lại yêu cầu quản trị này trước khi thực hiện. "
                "Bạn hãy diễn đạt rõ người dùng và thao tác cần làm."
            )
            return SkillResult(
                reply=reply,
                status="destructive_fallback_blocked",
                success=False,
                resulting_state=reply,
                error_code="destructive_fallback_blocked",
                error_message=f"legacy admin write blocked: {command.action}",
                resolved_entities=["access_control", command.action],
            )
        result = execute_admin_command(context.conn, context.adapter_input.channel, context.adapter_input.user_id, command)
        success = result.reply != "Bạn không có quyền quản trị."
        if success and command.action in {"count_members", "list_members"}:
            save_context(
                context.conn,
                channel=context.adapter_input.channel,
                user_id=context.adapter_input.user_id,
                state="user_management_list",
                payload={"last_action": command.action},
                ttl_seconds=300,
                source_message_id=context.adapter_input.source_message_id,
            )
        return SkillResult(
            reply=result.reply,
            status="admin_command",
            success=success,
            resulting_state=result.reply,
            error_code=None if success else "permission_denied",
            error_message=None if success else "actor is not admin",
            notifications=result.notifications or [],
        )

    def _match_user_list_follow_up(self, context: SkillContext) -> SkillRoute | None:
        if normalized_text(context.adapter_input.text) not in USER_LIST_FOLLOW_UPS:
            return None
        active = get_active_context(
            context.conn,
            channel=context.adapter_input.channel,
            user_id=context.adapter_input.user_id,
        )
        if not active or active.state != "user_management_list":
            return None
        return SkillRoute(
            intent="admin_command",
            skill=self.name,
            confidence=0.98,
            entities={"action": "list_members", "from_context": True},
            requires_tool=True,
            permission_action="list_members",
        )

    def _match_contextual_revoke(self, context: SkillContext) -> SkillRoute | None:
        normalized = normalized_text(context.adapter_input.text)
        if not normalized.startswith(CONTEXTUAL_REVOKE_PREFIXES):
            return None
        active = get_active_context(
            context.conn,
            channel=context.adapter_input.channel,
            user_id=context.adapter_input.user_id,
        )
        if not active or active.state != "user_management_list":
            return None
        prefix = next(prefix for prefix in CONTEXTUAL_REVOKE_PREFIXES if normalized.startswith(prefix))
        target = normalized[len(prefix):].strip()
        target = target.removesuffix(" di").removesuffix(" nhe").strip()
        if not target:
            return None
        return SkillRoute(
            intent="admin_command",
            skill=self.name,
            confidence=0.96,
            entities={"action": "revoke_by_query", "target_query": target, "from_context": True},
            requires_tool=True,
            permission_action="revoke_by_query",
        )

    def _command_from_route(self, context: SkillContext, route: SkillRoute) -> AdminCommand:
        if route.entities.get("from_context") and route.entities.get("action") == "list_members":
            return AdminCommand("list_members")
        if route.entities.get("from_context") and route.entities.get("action") == "revoke_by_query":
            return AdminCommand("revoke_by_query", target_query=str(route.entities.get("target_query") or ""))
        command = parse_admin_command(context.adapter_input.text)
        assert command is not None
        return command
