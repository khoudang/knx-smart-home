from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from smart_home_agent.agent_core.command_models import AdapterInput
from smart_home_agent.agent_core.entity_resolver import EntityResolver, format_user_options
from smart_home_agent.agent_core.intent_schema import IntentClassification
from smart_home_agent.integrations.device_control import DeviceControlClient
from smart_home_agent.services.access_control import (
    AdminCommand,
    create_access_request,
    execute_admin_command,
)
from smart_home_agent.services.pending_actions import PendingAction, create_pending_action, mark_pending_action
from smart_home_agent.services.pipeline_log import log_pipeline_event
from smart_home_agent.services.user_repository import resolve_user_role
from smart_home_agent.services.user_repository import UserRole
from smart_home_agent.integrations.deepseek_client import DeepSeekIntentParser
from smart_home_agent.skills.base import SkillContext, SkillResult
from smart_home_agent.skills.external.weather import WeatherSkill
from smart_home_agent.skills.external.web_search import WebSearchSkill
from smart_home_agent.skills.home.device_control import DeviceControlSkill
from smart_home_agent.skills.home.scene import SceneSkill


CONFIRMATION_REQUIRED_INTENTS = {"remove_user", "revoke_all_permissions", "block_user"}
ADMIN_INTENT_ACTIONS = {
    "list_users": "list_members",
    "list_blocked_users": "list_blocked",
    "list_pending_requests": "list_requests",
    "check_user_permission": "list_members",
}


@dataclass(frozen=True)
class IntentRouterResult:
    handled: bool
    result: SkillResult | None = None
    fallback_reason: str | None = None
    resolved_entities: list[str] = field(default_factory=list)


class IntentRouter:
    def __init__(self, conn: sqlite3.Connection, min_confidence: float = 0.55) -> None:
        self.conn = conn
        self.min_confidence = min_confidence
        self.resolver = EntityResolver(conn)
        self.weather_skill = WeatherSkill()
        self.web_search_skill = WebSearchSkill()
        self.scene_skill = SceneSkill()
        self.device_control_skill = DeviceControlSkill()

    def route(
        self,
        *,
        adapter_input: AdapterInput,
        user_role: UserRole,
        classification: IntentClassification,
        intent_parser: DeepSeekIntentParser,
        device_client: DeviceControlClient,
    ) -> IntentRouterResult:
        if classification.confidence < self.min_confidence:
            return IntentRouterResult(False, fallback_reason="classifier_low_confidence")
        if classification.needs_clarification:
            reply = classification.clarification_question or "Bạn muốn mình xử lý việc gì?"
            return IntentRouterResult(
                True,
                SkillResult(
                    reply=reply,
                    status="clarification",
                    success=False,
                    resulting_state=reply,
                    error_code="classifier_needs_clarification",
                    error_message="classifier requested clarification",
                    resolved_entities=[classification.domain, classification.intent],
                ),
            )
        if classification.domain == "weather":
            return self._route_weather(adapter_input, user_role, classification, intent_parser, device_client)
        if classification.domain == "web_search":
            return self._route_web_search(adapter_input, user_role, classification, intent_parser, device_client)
        if classification.domain == "scene":
            return self._route_scene(adapter_input, user_role, classification, intent_parser, device_client)
        if classification.domain == "device_control":
            return self._route_device_control(adapter_input, user_role, classification, intent_parser, device_client)
        if classification.domain != "access_control":
            return IntentRouterResult(False, fallback_reason=f"domain_not_migrated:{classification.domain}")
        return self._route_access_control(adapter_input, user_role, classification)

    def _context(
        self,
        adapter_input: AdapterInput,
        user_role: UserRole,
        intent_parser: DeepSeekIntentParser,
        device_client: DeviceControlClient,
    ) -> SkillContext:
        return SkillContext(
            conn=self.conn,
            adapter_input=adapter_input,
            user_role=user_role,
            intent_parser=intent_parser,
            device_client=device_client,
        )

    def _route_weather(
        self,
        adapter_input: AdapterInput,
        user_role: UserRole,
        classification: IntentClassification,
        intent_parser: DeepSeekIntentParser,
        device_client: DeviceControlClient,
    ) -> IntentRouterResult:
        route = self.weather_skill.route_from_classification(classification)
        log_pipeline_event(
            event="weather_request_resolved",
            request_id=adapter_input.request_id,
            actor_id=adapter_input.user_id,
            channel=adapter_input.channel,
            domain="weather",
            intent=classification.intent,
            confidence=classification.confidence,
            location=route.entities.get("location"),
            date_reference=route.entities.get("date_reference"),
            forecast_days=route.entities.get("forecast_days"),
            weather_attribute=route.entities.get("weather_attribute"),
        )
        result = self.weather_skill.execute(
            SkillContext(
                conn=self.conn,
                adapter_input=adapter_input,
                user_role=user_role,
                intent_parser=intent_parser,
                device_client=device_client,
            ),
            route,
        )
        log_pipeline_event(
            event="weather_executor_completed" if result.success else "weather_executor_failed",
            request_id=adapter_input.request_id,
            actor_id=adapter_input.user_id,
            channel=adapter_input.channel,
            domain="weather",
            intent=classification.intent,
            location=route.entities.get("location"),
            date_reference=route.entities.get("date_reference"),
            forecast_days=route.entities.get("forecast_days"),
            weather_attribute=route.entities.get("weather_attribute"),
            execution_result=result.status,
            error_code=result.error_code,
        )
        return IntentRouterResult(True, result, resolved_entities=result.resolved_entities)

    def _route_web_search(
        self,
        adapter_input: AdapterInput,
        user_role: UserRole,
        classification: IntentClassification,
        intent_parser: DeepSeekIntentParser,
        device_client: DeviceControlClient,
    ) -> IntentRouterResult:
        route = self.web_search_skill.route_from_classification(classification)
        log_pipeline_event(
            event="web_search_request_resolved",
            request_id=adapter_input.request_id,
            actor_id=adapter_input.user_id,
            channel=adapter_input.channel,
            domain="web_search",
            intent=classification.intent,
            confidence=classification.confidence,
            search_source=route.entities.get("preferred_source"),
        )
        result = self.web_search_skill.execute(
            SkillContext(
                conn=self.conn,
                adapter_input=adapter_input,
                user_role=user_role,
                intent_parser=intent_parser,
                device_client=device_client,
            ),
            route,
        )
        log_pipeline_event(
            event="web_search_executor_completed" if result.success else "web_search_executor_failed",
            request_id=adapter_input.request_id,
            actor_id=adapter_input.user_id,
            channel=adapter_input.channel,
            domain="web_search",
            intent=classification.intent,
            search_source=route.entities.get("preferred_source"),
            result_count=_result_count(result.resolved_entities),
            execution_result=result.status,
            error_code=result.error_code,
        )
        return IntentRouterResult(True, result, resolved_entities=result.resolved_entities)

    def _route_scene(
        self,
        adapter_input: AdapterInput,
        user_role: UserRole,
        classification: IntentClassification,
        intent_parser: DeepSeekIntentParser,
        device_client: DeviceControlClient,
    ) -> IntentRouterResult:
        route = self.scene_skill.route_from_classification(classification)
        log_pipeline_event(
            event="scene_request_resolved",
            request_id=adapter_input.request_id,
            actor_id=adapter_input.user_id,
            channel=adapter_input.channel,
            domain="scene",
            intent=classification.intent,
            confidence=classification.confidence,
            scene_name=route.entities.get("scene_name"),
            action=route.entities.get("action"),
        )
        result = self.scene_skill.execute(self._context(adapter_input, user_role, intent_parser, device_client), route)
        log_pipeline_event(
            event="scene_executor_completed" if result.success else "scene_executor_failed",
            request_id=adapter_input.request_id,
            actor_id=adapter_input.user_id,
            channel=adapter_input.channel,
            domain="scene",
            intent=classification.intent,
            scene_name=route.entities.get("scene_name"),
            action=route.entities.get("action"),
            execution_result=result.status,
            error_code=result.error_code,
            dry_run=True,
        )
        return IntentRouterResult(True, result, resolved_entities=result.resolved_entities)

    def _route_device_control(
        self,
        adapter_input: AdapterInput,
        user_role: UserRole,
        classification: IntentClassification,
        intent_parser: DeepSeekIntentParser,
        device_client: DeviceControlClient,
    ) -> IntentRouterResult:
        route = self.device_control_skill.route_from_classification(classification)
        log_pipeline_event(
            event="device_request_resolved",
            request_id=adapter_input.request_id,
            actor_id=adapter_input.user_id,
            channel=adapter_input.channel,
            domain="device_control",
            intent=classification.intent,
            confidence=classification.confidence,
            device_type=route.entities.get("device_type"),
            room_name=route.entities.get("room"),
            action=route.intent,
            value=(route.parsed_intent.parameters or {}).get("brightness") if route.parsed_intent else None,
            unit=classification.entities.get("unit"),
            dry_run=True,
        )
        validated = self.device_control_skill.validate(
            self._context(adapter_input, user_role, intent_parser, device_client),
            route,
        )
        result = self.device_control_skill.execute(
            self._context(adapter_input, user_role, intent_parser, device_client),
            validated,
        )
        event = "device_executor_completed" if result.success or result.status == "dry_run" else "device_executor_failed"
        if result.status == "authorization_failed":
            event = "device_permission_denied"
        if result.status == "confirmation_required":
            event = "device_confirmation_required"
        if result.error_code == "capability_mismatch":
            event = "device_capability_rejected"
        log_pipeline_event(
            event=event,
            request_id=adapter_input.request_id,
            actor_id=adapter_input.user_id,
            channel=adapter_input.channel,
            domain="device_control",
            intent=classification.intent,
            action=route.intent,
            resolved_device_count=len(result.resolved_device_ids),
            permission_result="allowed" if result.success else "denied_or_failed",
            confirmation_required=result.status == "confirmation_required",
            dry_run=True,
            execution_result=result.status,
            error_code=result.error_code,
        )
        return IntentRouterResult(True, result, resolved_entities=result.resolved_entities)

    def _route_access_control(
        self,
        adapter_input: AdapterInput,
        user_role: UserRole,
        classification: IntentClassification,
    ) -> IntentRouterResult:
        intent = classification.intent
        if intent == "request_access":
            result = create_access_request(
                self.conn,
                channel=adapter_input.channel,
                user_id=adapter_input.user_id,
                display_name=adapter_input.display_name,
                username=adapter_input.username,
                source_message_id=adapter_input.source_message_id,
            )
            reply_status = f"access_request_{result.status}"
            return IntentRouterResult(
                True,
                SkillResult(
                    reply=result.reply,
                    status=reply_status,
                    success=result.status in {"pending", "already_authorized"},
                    resulting_state=result.reply,
                    notifications=result.notifications,
                    resolved_entities=["access_control", intent],
                ),
            )

        if not user_role.known or not user_role.enabled or user_role.role not in {"owner", "admin"}:
            log_pipeline_event(
                event="permission_denied",
                request_id=adapter_input.request_id,
                actor_id=adapter_input.user_id,
                channel=adapter_input.channel,
                domain="access_control",
                intent=intent,
                permission_result="denied",
            )
            reply = "Bạn không có quyền quản trị."
            return IntentRouterResult(
                True,
                SkillResult(
                    reply=reply,
                    status="admin_command",
                    success=False,
                    resulting_state=reply,
                    error_code="permission_denied",
                    error_message="actor is not admin",
                    resolved_entities=["access_control", intent],
                ),
            )

        if intent in ADMIN_INTENT_ACTIONS:
            return self._execute_admin(adapter_input, AdminCommand(ADMIN_INTENT_ACTIONS[intent]), intent)
        if intent == "approve_access":
            request_id = _optional_int(classification.entities.get("request_id"))
            action = "approve" if request_id is not None else "approve_current"
            command = AdminCommand(action, request_id=request_id, role=str(classification.entities.get("role") or "member"))
            return self._execute_admin(adapter_input, command, intent)
        if intent == "reject_access":
            request_id = _optional_int(classification.entities.get("request_id"))
            action = "reject" if request_id is not None else "reject_current"
            return self._execute_admin(adapter_input, AdminCommand(action, request_id=request_id), intent)
        if intent == "unblock_user":
            return self._execute_user_target(adapter_input, classification, action="enable_by_query", prefer_blocked=True)
        if intent == "grant_permission":
            return self._grant_permission(adapter_input, classification)
        if intent in {"revoke_permission", "remove_user"}:
            if intent in CONFIRMATION_REQUIRED_INTENTS:
                return self._create_user_target_pending(adapter_input, classification, action="revoke_by_query")
            return self._execute_user_target(adapter_input, classification, action="revoke_by_query")
        if intent == "block_user":
            return self._create_user_target_pending(adapter_input, classification, action="revoke_by_query")
        if intent == "revoke_all_permissions":
            reply = "Thu hồi toàn bộ quyền là thao tác nguy hiểm. Chức năng này chưa được bật tự động."
            return IntentRouterResult(
                True,
                SkillResult(
                    reply=reply,
                    status="confirmation_required",
                    success=False,
                    resulting_state=reply,
                    error_code="unsupported_bulk_admin_action",
                    error_message="revoke_all_permissions requires a separate audited bulk executor",
                    resolved_entities=["access_control", intent],
                ),
            )
        return IntentRouterResult(False, fallback_reason=f"unsupported_access_intent:{intent}")

    def execute_pending_action(
        self,
        *,
        adapter_input: AdapterInput,
        user_role: UserRole,
        action: PendingAction,
    ) -> SkillResult:
        if action.status != "pending":
            log_pipeline_event(
                event="confirmation_replay_blocked",
                request_id=adapter_input.request_id,
                pending_action_id=action.id,
                actor_id=adapter_input.user_id,
                channel=adapter_input.channel,
                intent=action.intent,
                execution_attempt=0,
                execution_result="replay_blocked",
                replay_blocked=True,
            )
            reply = "Yêu cầu xác nhận này đã được xử lý trước đó."
            return SkillResult(
                reply=reply,
                status="confirmation_replay_blocked",
                success=False,
                resulting_state=reply,
                error_code="confirmation_replay_blocked",
                error_message=f"pending action is {action.status}",
                resolved_entities=["access_control", action.intent, f"pending_action:{action.id}"],
            )
        if not user_role.known or not user_role.enabled or user_role.role not in {"owner", "admin"}:
            log_pipeline_event(
                event="permission_denied",
                request_id=adapter_input.request_id,
                pending_action_id=action.id,
                actor_id=adapter_input.user_id,
                channel=adapter_input.channel,
                intent=action.intent,
                execution_attempt=1,
                execution_result="permission_denied",
                replay_blocked=False,
            )
            reply = "Bạn không còn quyền quản trị để xác nhận thao tác này."
            return SkillResult(
                reply=reply,
                status="confirmation_denied",
                success=False,
                resulting_state=reply,
                error_code="permission_denied",
                error_message="actor is not admin at confirmation time",
                resolved_entities=["access_control", action.intent, f"pending_action:{action.id}"],
            )
        entities = action.resolved_entities
        command = AdminCommand(
            str(entities["admin_action"]),
            target_query=f"{entities['target_channel']} {entities['target_user_id']}",
        )
        state_before = _user_state(self.conn, entities["target_channel"], entities["target_user_id"])
        result = execute_admin_command(self.conn, adapter_input.channel, adapter_input.user_id, command)
        mark_pending_action(self.conn, action.id, status="completed", completed_request_id=adapter_input.request_id)
        success = result.reply != "Bạn không có quyền quản trị."
        state_after = _user_state(self.conn, entities["target_channel"], entities["target_user_id"])
        log_pipeline_event(
            event="confirmation_executed",
            request_id=adapter_input.request_id,
            pending_action_id=action.id,
            actor_id=adapter_input.user_id,
            channel=adapter_input.channel,
            intent=action.intent,
            execution_attempt=1,
            execution_result="success" if success else "failed",
            state_before=state_before,
            state_after=state_after,
            replay_blocked=False,
        )
        return SkillResult(
            reply=result.reply,
            status="admin_command",
            success=success,
            resulting_state=result.reply,
            error_code=None if success else "permission_denied",
            error_message=None if success else "actor is not admin",
            notifications=result.notifications or [],
            resolved_entities=[
                "access_control",
                action.intent,
                f"pending_action:{action.id}",
                f"user:{entities['target_channel']}:{entities['target_user_id']}",
            ],
        )

    def _execute_user_target(
        self,
        adapter_input: AdapterInput,
        classification: IntentClassification,
        *,
        action: str,
        prefer_blocked: bool = False,
    ) -> IntentRouterResult:
        target = _target_user_query(classification)
        resolution = self.resolver.resolve_user(target, prefer_blocked=prefer_blocked)
        if resolution.status == "not_found":
            log_pipeline_event(
                event="entity_resolution_failed",
                request_id=adapter_input.request_id,
                actor_id=adapter_input.user_id,
                channel=adapter_input.channel,
                domain="access_control",
                intent=classification.intent,
                entity_resolution_status="not_found",
            )
            reply = "Không tìm thấy người dùng phù hợp. Hãy thử bằng tên, username hoặc user ID đầy đủ."
            return IntentRouterResult(
                True,
                SkillResult(
                    reply=reply,
                    status="admin_command",
                    success=False,
                    resulting_state=reply,
                    error_code="user_not_found",
                    error_message=f"target not found: {target}",
                    resolved_entities=["access_control", classification.intent],
                ),
            )
        if resolution.status == "ambiguous":
            log_pipeline_event(
                event="entity_resolution_failed",
                request_id=adapter_input.request_id,
                actor_id=adapter_input.user_id,
                channel=adapter_input.channel,
                domain="access_control",
                intent=classification.intent,
                entity_resolution_status="ambiguous",
            )
            reply = f"Tìm thấy nhiều người dùng phù hợp: {format_user_options(resolution.matches)}. Hãy gửi rõ hơn bằng username hoặc user ID."
            return IntentRouterResult(
                True,
                SkillResult(
                    reply=reply,
                    status="admin_command",
                    success=False,
                    resulting_state=reply,
                    error_code="ambiguous_user",
                    error_message=f"ambiguous target: {target}",
                    resolved_entities=["access_control", classification.intent],
                ),
            )
        user = resolution.single
        assert user is not None
        return self._execute_admin(
            adapter_input,
            AdminCommand(action, target_query=f"{user.channel} {user.user_id}"),
            classification.intent,
            resolved_entities=["access_control", classification.intent, f"user:{user.channel}:{user.user_id}"],
        )

    def _create_user_target_pending(
        self,
        adapter_input: AdapterInput,
        classification: IntentClassification,
        *,
        action: str,
    ) -> IntentRouterResult:
        target = _target_user_query(classification)
        resolution = self.resolver.resolve_user(target)
        if resolution.status == "not_found":
            log_pipeline_event(
                event="entity_resolution_failed",
                request_id=adapter_input.request_id,
                actor_id=adapter_input.user_id,
                channel=adapter_input.channel,
                domain="access_control",
                intent=classification.intent,
                entity_resolution_status="not_found",
            )
            reply = "Không tìm thấy người dùng phù hợp. Hãy thử bằng tên, username hoặc user ID đầy đủ."
            return IntentRouterResult(
                True,
                SkillResult(
                    reply=reply,
                    status="admin_command",
                    success=False,
                    resulting_state=reply,
                    error_code="user_not_found",
                    error_message=f"target not found: {target}",
                    resolved_entities=["access_control", classification.intent],
                ),
            )
        if resolution.status == "ambiguous":
            log_pipeline_event(
                event="entity_resolution_failed",
                request_id=adapter_input.request_id,
                actor_id=adapter_input.user_id,
                channel=adapter_input.channel,
                domain="access_control",
                intent=classification.intent,
                entity_resolution_status="ambiguous",
            )
            reply = f"Tìm thấy nhiều người dùng phù hợp: {format_user_options(resolution.matches)}. Hãy gửi rõ hơn bằng username hoặc user ID."
            return IntentRouterResult(
                True,
                SkillResult(
                    reply=reply,
                    status="admin_command",
                    success=False,
                    resulting_state=reply,
                    error_code="ambiguous_user",
                    error_message=f"ambiguous target: {target}",
                    resolved_entities=["access_control", classification.intent],
                ),
            )
        user = resolution.single
        assert user is not None
        pending = create_pending_action(
            self.conn,
            actor_id=adapter_input.user_id,
            channel=adapter_input.channel,
            intent=classification.intent,
            resolved_entities={
                "admin_action": action,
                "target_channel": user.channel,
                "target_user_id": user.user_id,
                "target_display_name": user.display_name,
                "target_username": user.username,
            },
            source_request_id=adapter_input.request_id,
        )
        log_pipeline_event(
            event="confirmation_created",
            request_id=adapter_input.request_id,
            actor_id=adapter_input.user_id,
            channel=adapter_input.channel,
            domain="access_control",
            intent=classification.intent,
            pending_action_id=pending.id,
            entity_resolution_status="resolved",
            resolved_entity_ids=[f"user:{user.channel}:{user.user_id}"],
        )
        label = user.display_name or user.username or f"{user.channel}:{user.user_id}"
        reply = f"Bạn xác nhận muốn thu hồi quyền của {label}? Trả lời 'đồng ý' để thực hiện hoặc 'hủy' để bỏ qua."
        return IntentRouterResult(
            True,
            SkillResult(
                reply=reply,
                status="confirmation_required",
                success=False,
                resulting_state=reply,
                error_code="confirmation_required",
                error_message=f"{classification.intent} requires confirmation",
                resolved_entities=[
                    "access_control",
                    classification.intent,
                    f"pending_action:{pending.id}",
                    f"user:{user.channel}:{user.user_id}",
                ],
            ),
        )

    def _grant_permission(self, adapter_input: AdapterInput, classification: IntentClassification) -> IntentRouterResult:
        target = _target_user_query(classification)
        resolution = self.resolver.resolve_user(target)
        if resolution.status != "resolved":
            reply = "Mình chưa xác định được người dùng cần cấp quyền. Hãy dùng tên, username hoặc user ID rõ hơn."
            return IntentRouterResult(
                True,
                SkillResult(
                    reply=reply,
                    status="admin_command",
                    success=False,
                    resulting_state=reply,
                    error_code="user_not_found" if resolution.status == "not_found" else "ambiguous_user",
                    error_message=f"grant target resolution: {resolution.status}",
                    resolved_entities=["access_control", classification.intent],
                ),
            )
        user = resolution.single
        assert user is not None
        role = str(classification.entities.get("role") or "member")
        return self._execute_admin(
            adapter_input,
            AdminCommand("update_role", channel=user.channel, user_id=user.user_id, role=role),
            classification.intent,
            resolved_entities=["access_control", classification.intent, f"user:{user.channel}:{user.user_id}"],
        )

    def _execute_admin(
        self,
        adapter_input: AdapterInput,
        command: AdminCommand,
        intent: str,
        resolved_entities: list[str] | None = None,
    ) -> IntentRouterResult:
        result = execute_admin_command(self.conn, adapter_input.channel, adapter_input.user_id, command)
        success = result.reply != "Bạn không có quyền quản trị."
        return IntentRouterResult(
            True,
            SkillResult(
                reply=result.reply,
                status="admin_command",
                success=success,
                resulting_state=result.reply,
                error_code=None if success else "permission_denied",
                error_message=None if success else "actor is not admin",
                notifications=result.notifications or [],
                resolved_entities=resolved_entities or ["access_control", intent],
            ),
            resolved_entities=resolved_entities or ["access_control", intent],
        )


def _target_user_query(classification: IntentClassification) -> str:
    for key in ("target_user_query", "user_query", "name", "username", "user_id"):
        value = classification.entities.get(key)
        if value:
            return str(value)
    return ""


def _optional_int(value) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _result_count(resolved_entities: list[str]) -> int | None:
    for item in resolved_entities:
        if not item.startswith("result_count:"):
            continue
        try:
            return int(item.split(":", 1)[1])
        except ValueError:
            return None
    return None


def _user_state(conn: sqlite3.Connection, channel: str, user_id: str) -> dict:
    role = resolve_user_role(conn, channel, user_id)
    return {
        "channel": channel,
        "user_id": user_id,
        "known": role.known,
        "role": role.role,
        "enabled": role.enabled,
    }
