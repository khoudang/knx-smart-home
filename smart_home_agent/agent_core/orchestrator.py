from __future__ import annotations

import os
import sqlite3
import time
import uuid
import hashlib
from dataclasses import dataclass
from pathlib import Path

from smart_home_agent.agent_core.command_models import AdapterInput
from smart_home_agent.agent_core.intent_classifier import DeepSeekIntentClassifier, IntentClassifier
from smart_home_agent.agent_core.intent_router import IntentRouter
from smart_home_agent.agent_core.intent_schema import IntentSchemaError
from smart_home_agent.integrations.deepseek_client import DeepSeekIntentParser
from smart_home_agent.integrations.device_control import DeviceControlClient
from smart_home_agent.integrations.knx_bridge_client import KNXBridgeClient
from smart_home_agent.services.command_log import begin_command, complete_command, find_by_source_message, record_execution
from smart_home_agent.services.device_resolver import (
    AmbiguousDevice,
    CapabilityMismatch,
    DeviceNotFound,
    DisabledDevice,
    NoDevicesConfigured,
)
from smart_home_agent.services.access_control import expire_guests
from smart_home_agent.services.pending_actions import confirmation_decision, get_active_pending_action, mark_pending_action
from smart_home_agent.services.pipeline_log import log_pipeline_event
from smart_home_agent.services.user_repository import resolve_user_role
from smart_home_agent.skills.base import SkillContext
from smart_home_agent.skills.registry import build_default_registry
from smart_home_agent.skills.router import SkillRouter
from smart_home_agent.storage.sqlite import connect, migrate


@dataclass(frozen=True)
class AgentResponse:
    reply: str
    status: str
    request_id: str
    executed: bool = False
    notifications: list[dict] | None = None


class Orchestrator:
    def __init__(
        self,
        conn: sqlite3.Connection,
        intent_parser: DeepSeekIntentParser | None = None,
        intent_classifier: IntentClassifier | None = None,
        device_client: DeviceControlClient | None = None,
    ) -> None:
        self.conn = conn
        self.intent_parser = intent_parser or DeepSeekIntentParser.from_env()
        self.intent_classifier = intent_classifier or DeepSeekIntentClassifier.from_env()
        self.device_client = device_client or KNXBridgeClient.from_env()
        self.skill_router = SkillRouter(build_default_registry())
        self.intent_router = IntentRouter(conn)
        self._pipeline_log_fields: dict = {}

    @classmethod
    def from_env(cls) -> "Orchestrator":
        db_path = Path(os.environ.get("DATABASE_PATH", "data/smart_home.db"))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = connect(db_path)
        migrate(conn)
        return cls(conn)

    def handle(self, adapter_input: AdapterInput) -> AgentResponse:
        existing = find_by_source_message(self.conn, adapter_input.channel, adapter_input.source_message_id)
        if existing:
            return AgentResponse(
                reply=existing["resulting_state"] or "Yêu cầu này đã được xử lý trước đó.",
                status=existing["status"] or "duplicate",
                request_id=existing["request_id"],
                executed=False,
            )

        started = time.perf_counter()
        begin_command(self.conn, adapter_input)
        parsed_intent = None
        self._pipeline_log_fields = {}
        try:
            expire_guests(self.conn)
            user_role = resolve_user_role(self.conn, adapter_input.channel, adapter_input.user_id)
            if not user_role.enabled or user_role.role == "blocked":
                complete_command(
                    self.conn,
                    adapter_input.request_id,
                    status="authorization_failed",
                    success=False,
                    error_code="user_disabled",
                    error_message="user is disabled",
                    latency_ms=_latency_ms(started),
                )
                return AgentResponse(
                    "Tài khoản này không được phép sử dụng hệ thống.",
                    "authorization_failed",
                    adapter_input.request_id,
                )

            confirmation_result = self._try_confirmation_response(adapter_input, user_role)
            if confirmation_result is not None:
                skill_result = confirmation_result
                fallback_reason = None
            else:
                skill_result, fallback_reason = self._try_intent_pipeline(adapter_input, user_role)
            used_legacy_fallback = skill_result is None

            context = SkillContext(
                conn=self.conn,
                adapter_input=adapter_input,
                user_role=user_role,
                intent_parser=self.intent_parser,
                device_client=self.device_client,
                block_legacy_admin_writes=fallback_reason is not None,
            )
            if skill_result is None:
                route, skill_result = self.skill_router.execute(context)
            parsed_intent = skill_result.parsed_intent

            if not user_role.known and skill_result.status not in {
                "access_request_pending",
                "access_request_already_authorized",
                "help",
                "general_chat",
                "greeting",
            }:
                reply = "Bạn chưa được cấp quyền. Hãy gửi 'xin quyền' để yêu cầu chủ nhà phê duyệt."
                complete_command(
                    self.conn,
                    adapter_input.request_id,
                    status="authorization_failed",
                    success=False,
                    error_code="unknown_user",
                    error_message="unknown user must request access before using bot",
                    latency_ms=_latency_ms(started),
                )
                return AgentResponse(reply, "authorization_failed", adapter_input.request_id)

            if skill_result.command_execution:
                record_execution(
                    self.conn,
                    request_id=adapter_input.request_id,
                    **skill_result.command_execution,
                )

            log_pipeline_event(
                event=_event_from_result(skill_result.status, used_legacy_fallback),
                request_id=adapter_input.request_id,
                actor_id=adapter_input.user_id,
                channel=adapter_input.channel,
                message_hash=_message_hash(adapter_input.text),
                **self._pipeline_log_fields,
                resolved_entity_ids=skill_result.resolved_entities,
                permission_result="allowed" if skill_result.success else "denied_or_failed",
                fallback_used=used_legacy_fallback,
                fallback_reason=fallback_reason,
                confirmation_state=_confirmation_state_from_status(skill_result.status),
                executor_result=skill_result.status,
                error_code=skill_result.error_code,
                latency_ms=_latency_ms(started),
            )
            complete_command(
                self.conn,
                adapter_input.request_id,
                status=skill_result.status,
                success=skill_result.success,
                parsed_intent=skill_result.parsed_intent,
                resolved_device_ids=skill_result.resolved_device_ids,
                resolved_entities=skill_result.resolved_entities,
                resulting_state=skill_result.resulting_state or skill_result.reply,
                error_code=skill_result.error_code,
                error_message=skill_result.error_message,
                sensitive=skill_result.sensitive,
                latency_ms=_latency_ms(started),
            )
            return AgentResponse(
                skill_result.reply,
                skill_result.status,
                adapter_input.request_id,
                executed=skill_result.executed,
                notifications=skill_result.notifications,
            )
        except (DeviceNotFound, DisabledDevice, CapabilityMismatch, AmbiguousDevice, ValueError) as exc:
            status = getattr(exc, "code", "validation_failed")
            message = _friendly_error(exc)
            self._log_device_resolution_error(adapter_input, exc, status)
            if isinstance(exc, AmbiguousDevice):
                names = ", ".join(f"{m.canonical_name} ({m.room})" for m in exc.matches)
                message = f"Lệnh bị mơ hồ. Bạn muốn điều khiển thiết bị nào: {names}?"
            complete_command(
                self.conn,
                adapter_input.request_id,
                status=status,
                success=False,
                parsed_intent=parsed_intent,
                error_code=status,
                error_message=message,
                latency_ms=_latency_ms(started),
            )
            return AgentResponse(message, status, adapter_input.request_id)
        except Exception as exc:
            complete_command(
                self.conn,
                adapter_input.request_id,
                status="external_service_error",
                success=False,
                parsed_intent=parsed_intent,
                error_code="unexpected_error",
                error_message=str(exc),
                latency_ms=_latency_ms(started),
            )
            return AgentResponse(
                "Hệ thống đang có lỗi, vui lòng thử lại sau.",
                "external_service_error",
                adapter_input.request_id,
            )


    def _log_device_resolution_error(self, adapter_input: AdapterInput, exc: Exception, status: str) -> None:
        if self._pipeline_log_fields.get("domain") != "device_control":
            return
        event = "device_executor_failed"
        entity_resolution_status = "failed"
        extra = {}
        if isinstance(exc, AmbiguousDevice):
            event = "device_resolution_ambiguous"
            entity_resolution_status = "ambiguous"
            extra["resolved_device_count"] = len(exc.matches)
        elif isinstance(exc, CapabilityMismatch):
            event = "device_capability_rejected"
            entity_resolution_status = "capability_rejected"
        elif isinstance(exc, DeviceNotFound):
            entity_resolution_status = "not_found"
        elif isinstance(exc, DisabledDevice):
            entity_resolution_status = "disabled"
        log_pipeline_event(
            event=event,
            request_id=adapter_input.request_id,
            actor_id=adapter_input.user_id,
            channel=adapter_input.channel,
            domain="device_control",
            intent=self._pipeline_log_fields.get("intent"),
            confidence=self._pipeline_log_fields.get("confidence"),
            entity_resolution_status=entity_resolution_status,
            execution_result=status,
            error_code=status,
            dry_run=True,
            **extra,
        )


    def _try_confirmation_response(self, adapter_input: AdapterInput, user_role):
        action = get_active_pending_action(
            self.conn,
            channel=adapter_input.channel,
            actor_id=adapter_input.user_id,
        )
        decision = confirmation_decision(adapter_input.text)
        if not action:
            if decision:
                log_pipeline_event(
                    event="confirmation_replay_blocked",
                    request_id=adapter_input.request_id,
                    actor_id=adapter_input.user_id,
                    channel=adapter_input.channel,
                    control_command=decision,
                    normalization_applied=True,
                    replay_blocked=True,
                )
            return None
        if not decision:
            return None
        self._pipeline_log_fields = {
            "control_command": decision,
            "normalization_applied": True,
        }
        if decision == "cancel":
            mark_pending_action(self.conn, action.id, status="cancelled", completed_request_id=adapter_input.request_id)
            log_pipeline_event(
                event="confirmation_cancelled",
                request_id=adapter_input.request_id,
                actor_id=adapter_input.user_id,
                channel=adapter_input.channel,
                pending_action_id=action.id,
                intent=action.intent,
                control_command="cancel",
                normalization_applied=True,
            )
            reply = "Đã hủy thao tác đang chờ xác nhận."
            from smart_home_agent.skills.base import SkillResult

            return SkillResult(
                reply=reply,
                status="confirmation_cancelled",
                success=True,
                resulting_state=reply,
                resolved_entities=["access_control", action.intent, f"pending_action:{action.id}"],
            )
        return self.intent_router.execute_pending_action(
            adapter_input=adapter_input,
            user_role=user_role,
            action=action,
        )

    def _try_intent_pipeline(self, adapter_input: AdapterInput, user_role):
        if os.environ.get("INTENT_CLASSIFIER_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            self._pipeline_log_fields = {"schema_valid": False}
            log_pipeline_event(
                event="intent_fallback_used",
                request_id=adapter_input.request_id,
                actor_id=adapter_input.user_id,
                channel=adapter_input.channel,
                fallback_reason="classifier_disabled",
            )
            return None, "classifier_disabled"
        try:
            classification = self.intent_classifier.classify(adapter_input.text)
        except IntentSchemaError:
            self._pipeline_log_fields = {"schema_valid": False}
            log_pipeline_event(
                event="intent_schema_rejected",
                request_id=adapter_input.request_id,
                actor_id=adapter_input.user_id,
                channel=adapter_input.channel,
                error_code="classifier_schema_invalid",
            )
            return None, "classifier_schema_invalid"
        except (ValueError, TypeError, TimeoutError):
            self._pipeline_log_fields = {"schema_valid": False}
            log_pipeline_event(
                event="intent_fallback_used",
                request_id=adapter_input.request_id,
                actor_id=adapter_input.user_id,
                channel=adapter_input.channel,
                fallback_reason="classifier_error",
            )
            return None, "classifier_error"
        self._pipeline_log_fields = {
            "domain": classification.domain,
            "intent": classification.intent,
            "confidence": classification.confidence,
            "schema_valid": True,
        }
        log_pipeline_event(
            event="intent_classified",
            request_id=adapter_input.request_id,
            actor_id=adapter_input.user_id,
            channel=adapter_input.channel,
            domain=classification.domain,
            intent=classification.intent,
            confidence=classification.confidence,
        )
        routed = self.intent_router.route(
            adapter_input=adapter_input,
            user_role=user_role,
            classification=classification,
            intent_parser=self.intent_parser,
            device_client=self.device_client,
        )
        if not routed.handled:
            log_pipeline_event(
                event="intent_fallback_used",
                request_id=adapter_input.request_id,
                actor_id=adapter_input.user_id,
                channel=adapter_input.channel,
                domain=classification.domain,
                intent=classification.intent,
                confidence=classification.confidence,
                fallback_reason=routed.fallback_reason or "pipeline_not_handled",
            )
            return None, routed.fallback_reason or "pipeline_not_handled"
        return routed.result, None


def adapter_input_from_payload(payload: dict) -> AdapterInput:
    channel = str(payload.get("source") or payload.get("channel") or "api")
    user_id = str(payload.get("user_id") or "anonymous")
    text = str(payload.get("message") or payload.get("text") or "")
    source_message_id = str(
        payload.get("source_message_id")
        or payload.get("message_id")
        or _stable_message_id(channel, user_id, text)
    )
    request_id = str(payload.get("request_id") or _request_id(channel, source_message_id))
    return AdapterInput(
        request_id=request_id,
        source_message_id=source_message_id,
        channel=channel,
        user_id=user_id,
        text=text,
        role=str(payload.get("role") or "guest"),
        display_name=_optional_payload_str(payload.get("display_name")),
        username=_optional_payload_str(payload.get("username")),
    )


def _optional_payload_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _stable_message_id(channel: str, user_id: str, text: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{channel}:{user_id}:{text}"))


def _request_id(channel: str, source_message_id: str) -> str:
    if source_message_id.startswith(f"{channel}:"):
        return source_message_id
    return f"{channel}:{source_message_id}"


def _latency_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _is_new_pipeline_status(status: str) -> bool:
    return status in {
        "admin_command",
        "confirmation_required",
        "confirmation_cancelled",
        "confirmation_denied",
        "confirmation_replay_blocked",
        "access_request_pending",
        "access_request_already_authorized",
        "access_request_blocked",
    }


def _confirmation_state_from_status(status: str) -> str | None:
    if status == "confirmation_required":
        return "pending"
    if status == "confirmation_cancelled":
        return "cancelled"
    if status in {"confirmation_denied", "confirmation_replay_blocked"}:
        return "blocked"
    if status == "admin_command":
        return "executed_or_not_required"
    return None


def _event_from_result(status: str, used_legacy_fallback: bool) -> str:
    if status == "destructive_fallback_blocked":
        return "destructive_fallback_blocked"
    if status == "confirmation_required":
        return "confirmation_created"
    if status == "confirmation_cancelled":
        return "confirmation_cancelled"
    if status == "confirmation_denied":
        return "permission_denied"
    if status == "confirmation_replay_blocked":
        return "confirmation_replay_blocked"
    if used_legacy_fallback:
        return "intent_fallback_used"
    if status in {"authorization_failed", "admin_command"}:
        return "executor_completed"
    return "executor_failed" if status.endswith("_failed") or status == "external_service_error" else "executor_completed"


def _message_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, NoDevicesConfigured):
        return "Chưa có thiết bị nào được cấu hình trong registry."
    if isinstance(exc, DeviceNotFound):
        return "Không tìm thấy thiết bị phù hợp."
    if isinstance(exc, DisabledDevice):
        return "Thiết bị này đang bị tắt trong registry."
    if isinstance(exc, CapabilityMismatch):
        return "Thiết bị không hỗ trợ thao tác này."
    if isinstance(exc, ValueError):
        return str(exc)
    return str(exc)
