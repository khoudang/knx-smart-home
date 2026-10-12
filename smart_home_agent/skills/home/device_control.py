from __future__ import annotations

from smart_home_agent.agent_core.command_models import Intent
from smart_home_agent.agent_core.intent_schema import IntentClassification
from smart_home_agent.agent_core.local_parser import parse_local, validate_parameters
from smart_home_agent.integrations.device_control import ControlCommand, ControlResult
from smart_home_agent.policies.safety import evaluate_policy
from smart_home_agent.services.device_resolver import resolve_device
from smart_home_agent.skills.base import BaseSkill, SkillContext, SkillResult, SkillRoute
from smart_home_agent.skills.common import looks_like_home_automation


CONTROL_INTENTS = {"turn_on", "turn_off", "set_brightness", "set_color", "read_state", "status_summary"}


class DeviceControlSkill(BaseSkill):
    name = "home.device_control"
    supported_intents = tuple(sorted(CONTROL_INTENTS))
    examples = ("bật đèn tròn", "tắt đèn phòng khách", "đặt đèn khách 70 phần trăm")
    required_role = "member"

    def match(self, context: SkillContext) -> SkillRoute | None:
        local = parse_local(context.adapter_input.text)
        if local and local.intent in CONTROL_INTENTS:
            return SkillRoute(
                intent=local.intent,
                skill=self.name,
                confidence=local.confidence,
                entities={"device_name": local.device_name, "room": local.room},
                requires_tool=True,
                parsed_intent=local,
                permission_action=local.intent,
            )
        if not local and looks_like_home_automation(context.conn, context.adapter_input.text):
            parsed = context.intent_parser.parse(context.adapter_input.text)
            if parsed.intent in CONTROL_INTENTS:
                return SkillRoute(
                    intent=parsed.intent,
                    skill=self.name,
                    confidence=parsed.confidence,
                    entities={"device_name": parsed.device_name, "room": parsed.room},
                    requires_tool=True,
                    parsed_intent=parsed,
                    permission_action=parsed.intent,
                )
        return None

    def route_from_classification(self, classification: IntentClassification) -> SkillRoute:
        intent = _intent_from_classification(classification)
        return SkillRoute(
            intent=intent.intent,
            skill=self.name,
            confidence=classification.confidence,
            entities={
                "device_name": intent.device_name,
                "room": intent.room,
                "device_type": classification.entities.get("device_type"),
                "scope": classification.entities.get("scope"),
            },
            requires_tool=True,
            parsed_intent=intent,
            permission_action=intent.intent,
        )

    def validate(self, context: SkillContext, route: SkillRoute) -> SkillRoute:
        if route.parsed_intent:
            validate_parameters(route.parsed_intent)
        return route

    def execute(self, context: SkillContext, route: SkillRoute) -> SkillResult:
        intent = route.parsed_intent or Intent(intent=route.intent, confidence=route.confidence)
        if intent.intent == "status_summary":
            return _home_status_summary(context, intent)
        if context.user_role.role in {"viewer", "guest"} and intent.intent != "read_state":
            return SkillResult(
                reply="Tài khoản này chỉ được xem thông tin, không được điều khiển thiết bị.",
                status="authorization_failed",
                success=False,
                parsed_intent=intent,
                error_code="role_not_allowed",
                error_message=f"{context.user_role.role} cannot execute device commands",
            )
        device = resolve_device(context.conn, intent)
        policy = evaluate_policy(intent, device, context.user_role.role)
        if not policy.allowed:
            return SkillResult(
                reply=policy.reason or "Lệnh cần xác nhận hoặc không được phép.",
                status=policy.status,
                success=False,
                parsed_intent=intent,
                resolved_device_ids=[device.device_id],
                resolved_entities=[ga for ga in _resolved_gas(device) if ga],
                error_code=policy.status,
                error_message=policy.reason,
                sensitive=device.is_sensitive,
            )
        expected_state = _expected_state(intent)
        result = _execute(context, intent, device, expected_state)
        reply = _reply_for_result(intent, result, device)
        success = result.status == "success"
        return SkillResult(
            reply=reply,
            status=result.status,
            success=success,
            parsed_intent=intent,
            resolved_device_ids=[device.device_id],
            resolved_entities=[ga for ga in _resolved_gas(device) if ga],
            resulting_state=reply,
            sensitive=device.is_sensitive,
            executed=success,
            command_execution={
                "device_id": device.device_id,
                "action": intent.intent,
                "parameters": intent.parameters,
                "expected_state": expected_state,
                "resulting_state": result.state or result.message,
                "status": result.status,
                "error_code": None if result.status in {"success", "accepted_but_unverified"} else result.status,
            },
        )


def _execute(context: SkillContext, intent: Intent, device, expected_state: dict | None) -> ControlResult:
    command = _control_command(intent, device, expected_state, context.adapter_input.request_id)
    if intent.intent == "read_state":
        return context.device_client.read_state(command)
    return context.device_client.execute(command)


def _expected_state(intent: Intent) -> dict | None:
    if intent.intent == "turn_on":
        return {"value": True}
    if intent.intent == "turn_off":
        return {"value": False}
    if intent.intent == "set_brightness":
        return {"value": intent.parameters["brightness"]}
    if intent.intent == "set_color":
        return {"value": intent.parameters.get("color") or intent.parameters}
    return None


def _control_command(intent: Intent, device, expected_state: dict | None, request_id: str) -> ControlCommand:
    value = None
    if intent.intent == "set_brightness":
        value = intent.parameters["brightness"]
    elif intent.intent == "set_color":
        value = intent.parameters.get("color") or intent.parameters
    return ControlCommand(
        device_id=device.device_id,
        device_type=device.device_type,
        action=intent.intent,
        control_ga=device.control_ga,
        status_ga=device.status_ga,
        dim_control_ga=device.dim_control_ga,
        dim_status_ga=device.dim_status_ga,
        color_control_ga=device.color_control_ga,
        color_status_ga=device.color_status_ga,
        value=value,
        expected_state=expected_state,
        request_id=request_id,
    )


def _reply_for_result(intent: Intent, result: ControlResult, device=None) -> str:
    if result.status == "success":
        return "Đã gửi lệnh và xác minh thành công."
    if result.status == "dry_run":
        action = {
            "turn_on": "bật",
            "turn_off": "tắt",
            "set_brightness": "đặt độ sáng",
            "set_color": "đổi màu",
            "read_state": "đọc trạng thái",
        }.get(intent.intent, "điều khiển")
        name = getattr(device, "canonical_name", None) or intent.device_name or "thiết bị"
        return f"Đã mô phỏng lệnh {action} {name}. Lệnh chưa được gửi xuống bus KNX."
    if result.status == "accepted_but_unverified":
        return "Đã gửi lệnh nhưng chưa xác minh được trạng thái."
    return result.message or result.status


def _resolved_gas(device) -> list[str | None]:
    return [
        device.control_ga,
        device.status_ga,
        device.dim_control_ga,
        device.dim_status_ga,
        device.color_control_ga,
        device.color_status_ga,
    ]


def _home_status_summary(context: SkillContext, intent: Intent) -> SkillResult:
    row = context.conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN device_type = 'light' THEN 1 ELSE 0 END) AS lights
        FROM devices
        WHERE enabled = 1 AND sync_status != 'deleted_from_sheet'
        """
    ).fetchone()
    total = int(row["total"] or 0)
    lights = int(row["lights"] or 0)
    reply = f"Registry hiện có {total} thiết bị đang bật cấu hình, trong đó có {lights} đèn."
    return SkillResult(
        reply=reply,
        status="status_summary",
        success=True,
        parsed_intent=intent,
        resulting_state=reply,
        resolved_entities=["device_control", "query_home_state"],
    )


def _intent_from_classification(classification: IntentClassification) -> Intent:
    entities = classification.entities
    device_name = _device_name(entities)
    room = _optional_text(entities.get("room_name") or entities.get("room"))
    if classification.intent == "set_device_value":
        value = _numeric_value(entities.get("value"))
        unit = str(entities.get("unit") or "").strip().lower()
        if unit and unit not in {"percent", "%", "phan tram", "percentage"}:
            raise ValueError("Đơn vị điều khiển chưa được hỗ trợ an toàn.")
        return Intent(
            intent="set_brightness",
            device_name=device_name,
            room=room,
            parameters={"brightness": value},
            confidence=classification.confidence,
        )
    if classification.intent in {"query_device_state", "query_room_state", "query_home_state"}:
        return Intent(
            intent="read_state" if device_name else "status_summary",
            device_name=device_name,
            room=room,
            parameters={},
            confidence=classification.confidence,
        )
    state = _state(entities.get("state"))
    action = {
        "on": "turn_on",
        "open": "turn_on",
        "unlock": "turn_on",
        "off": "turn_off",
        "closed": "turn_off",
        "close": "turn_off",
        "lock": "turn_off",
    }.get(state)
    if not action:
        raise ValueError("Trạng thái thiết bị chưa được hỗ trợ an toàn.")
    return Intent(intent=action, device_name=device_name, room=room, parameters={}, confidence=classification.confidence)


def _device_name(entities: dict) -> str | None:
    for key in ("device_name", "device_query", "name"):
        value = _optional_text(entities.get(key))
        if value:
            return value
    device_type = _optional_text(entities.get("device_type"))
    room = _optional_text(entities.get("room_name") or entities.get("room"))
    if device_type and room:
        return f"{device_type} {room}"
    return device_type


def _optional_text(value) -> str | None:
    text = str(value or "").strip()
    return text or None


def _state(value) -> str:
    return str(value or "").strip().lower()


def _numeric_value(value) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("Giá trị điều khiển phải là số.") from exc
    if number < 0 or number > 100:
        raise ValueError("Độ sáng phải là số nguyên từ 0 đến 100.")
    return number
