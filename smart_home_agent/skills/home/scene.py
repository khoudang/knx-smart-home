from __future__ import annotations

import json
import re
from dataclasses import dataclass

from smart_home_agent.agent_core.command_models import Intent
from smart_home_agent.agent_core.intent_schema import IntentClassification
from smart_home_agent.services.scene_service import create_scene_proposal
from smart_home_agent.skills.base import BaseSkill, SkillContext, SkillResult, SkillRoute
from smart_home_agent.skills.common import normalize_text, repair_mojibake


@dataclass(frozen=True)
class SceneCommand:
    action: str
    scene_name: str | None = None


class SceneSkill(BaseSkill):
    name = "home.scene"
    supported_intents = ("create_scene", "list_scenes", "run_scene")
    examples = ("tạo scene Đám cưới", "hiện tại hệ thống có scene gì", "chạy scene đi ngủ")
    required_role = "member"

    def match(self, context: SkillContext) -> SkillRoute | None:
        command = parse_scene_command(context.adapter_input.text)
        if not command:
            return None
        intent = "create_scene" if command.action == "create" else "run_scene" if command.action == "run" else "list_scenes"
        return SkillRoute(
            intent=intent,
            skill=self.name,
            confidence=0.96,
            entities={"action": command.action, "scene_name": command.scene_name},
            requires_tool=True,
            parsed_intent=Intent(intent="create_scene" if command.action == "create" else "run_scene", scene_name=command.scene_name, confidence=0.96)
            if command.action in {"create", "run"}
            else Intent(intent="list_scenes", confidence=0.96),
            permission_action=intent,
        )

    def route_from_classification(self, classification: IntentClassification) -> SkillRoute:
        action = _action_from_intent(classification.intent, classification.entities.get("scene_action"))
        scene_name = _scene_name_from_entities(classification)
        return SkillRoute(
            intent=classification.intent,
            skill=self.name,
            confidence=classification.confidence,
            entities={
                "action": action,
                "scene_name": scene_name,
                "room_name": classification.entities.get("room_name"),
            },
            requires_tool=True,
            parsed_intent=Intent(
                intent="list_scenes" if action in {"list", "query"} else "run_scene" if action in {"run", "deactivate"} else "create_scene",
                scene_name=scene_name,
                confidence=classification.confidence,
            ),
            permission_action=classification.intent,
        )

    def execute(self, context: SkillContext, route: SkillRoute) -> SkillResult:
        action = str(route.entities.get("action") or "")
        scene_name = str(route.entities.get("scene_name") or "").strip()
        if action == "list":
            reply = _format_scene_list(context)
            return SkillResult(reply=reply, status="list_scenes", resulting_state=reply)
        if action == "query":
            reply = _format_scene_query(context, scene_name)
            return SkillResult(
                reply=reply,
                status="query_scene" if "không tìm thấy" not in reply.lower() else "scene_not_found",
                success="không tìm thấy" not in reply.lower(),
                resulting_state=reply,
                parsed_intent=route.parsed_intent,
                error_code=None if "không tìm thấy" not in reply.lower() else "scene_not_found",
            )
        if action == "create":
            if not scene_name:
                reply = "Bạn muốn tạo scene tên gì?"
                return SkillResult(
                    reply=reply,
                    status="scene_clarification",
                    success=False,
                    resulting_state=reply,
                    error_code="missing_scene_name",
                    error_message="missing scene name",
                )
            proposal_id = create_scene_proposal(
                context.conn,
                proposed_name=scene_name,
                proposed_actions=[],
                evidence=f"requested by {context.adapter_input.channel}:{context.adapter_input.user_id}",
                confidence=0.7,
            )
            reply = (
                f"Mình đã tạo nháp scene '{scene_name}' với mã {proposal_id}. "
                "Scene này chưa có hành động thiết bị và chưa được chạy. "
                "Bạn có thể nói thêm: scene này gồm bật/tắt thiết bị nào."
            )
            return SkillResult(
                reply=reply,
                status="scene_proposal_created",
                resulting_state=reply,
                parsed_intent=route.parsed_intent,
            )
        if action == "run":
            reply = (
                "Mình đã hiểu đây là lệnh chạy scene, nhưng scene execution v2 chưa được bật để chạy live. "
                "Mình sẽ không chạy scene hoặc gửi lệnh thiết bị lúc này."
            )
            return SkillResult(
                reply=reply,
                status="scene_run_blocked",
                success=False,
                resulting_state=reply,
                parsed_intent=route.parsed_intent,
                error_code="scene_execution_disabled",
                error_message="scene execution is not enabled",
            )
        if action == "deactivate":
            reply = "Mình đã hiểu đây là lệnh tắt scene, nhưng scene deactivate chưa có executor an toàn. Mình sẽ không gửi lệnh thiết bị lúc này."
            return SkillResult(
                reply=reply,
                status="scene_run_blocked",
                success=False,
                resulting_state=reply,
                parsed_intent=route.parsed_intent,
                error_code="scene_execution_disabled",
                error_message="scene deactivate is not enabled",
            )
        reply = "Mình hiểu đây là thao tác scene, nhưng chưa rõ bạn muốn tạo, xem hay chạy scene."
        return SkillResult(
            reply=reply,
            status="scene_clarification",
            success=False,
            resulting_state=reply,
            error_code="scene_intent_unclear",
            error_message="scene action unclear",
        )


def parse_scene_command(text: str) -> SceneCommand | None:
    original = repair_mojibake(text).strip()
    normalized = normalize_text(original)
    if not _is_scene_text(normalized):
        return None
    for prefix in ("tao scene ", "them scene ", "tao canh ", "them canh ", "tao kich ban ", "them kich ban "):
        if normalized.startswith(prefix):
            return SceneCommand("create", _clean_scene_name(original, len(prefix)))
    if normalized in {
        "co scene gi",
        "co nhung scene gi",
        "danh sach scene",
        "danh sach kich ban",
        "liet ke scene",
        "liet ke kich ban",
        "hien tai he thong co cac scene gi",
        "he thong co cac scene gi",
        "co kich ban gi",
        "co nhung kich ban gi",
        "hien tai he thong co cac kich ban gi",
        "he thong co cac kich ban gi",
        "scene hien co",
        "kich ban hien co",
    }:
        return SceneCommand("list")
    for prefix in ("chay scene ", "goi scene ", "bat scene ", "kich hoat scene ", "chay kich ban ", "goi kich ban ", "bat kich ban ", "kich hoat kich ban "):
        if normalized.startswith(prefix):
            return SceneCommand("run", _clean_scene_name(original, len(prefix)))
    return SceneCommand("clarify")


def _action_from_intent(intent: str, scene_action) -> str:
    if intent == "list_scenes":
        return "list"
    if intent == "query_scene":
        return "query"
    if intent == "deactivate_scene":
        return "deactivate"
    if intent in {"activate_scene", "run_scene"}:
        return "run"
    if intent == "create_scene":
        return "create"
    normalized = normalize_text(str(scene_action or ""))
    if normalized in {"activate", "bat", "run", "chay", "kich hoat"}:
        return "run"
    if normalized in {"deactivate", "tat"}:
        return "deactivate"
    return "clarify"


def _scene_name_from_entities(classification: IntentClassification) -> str | None:
    for key in ("scene_name", "scene_query", "name"):
        value = classification.entities.get(key)
        if value:
            return str(value).strip()
    return None


def _is_scene_text(normalized: str) -> bool:
    return "scene" in normalized or "canh" in normalized or "kich ban" in normalized


def _clean_scene_name(original: str, prefix_length: int) -> str | None:
    words = original.strip().split()
    if len(words) <= 2:
        return None
    if normalize_text(" ".join(words[:2])) in {"tao scene", "them scene", "chay scene", "goi scene", "bat scene"}:
        name = " ".join(words[2:])
    elif normalize_text(" ".join(words[:2])) in {"tao canh", "them canh"}:
        name = " ".join(words[2:])
    elif normalize_text(" ".join(words[:4])) == "kich hoat kich ban":
        name = " ".join(words[4:])
    elif normalize_text(" ".join(words[:3])) in {
        "tao kich ban",
        "them kich ban",
        "chay kich ban",
        "goi kich ban",
        "bat kich ban",
    }:
        name = " ".join(words[3:])
    else:
        name = original[prefix_length:].strip()
    name = re.sub(r"\s+", " ", name).strip(" ?!.")
    return name or None


def _format_scene_list(context: SkillContext) -> str:
    rows = context.conn.execute(
        """
        SELECT scene_id, name, enabled, execution_count, last_executed_at
        FROM scenes
        ORDER BY updated_at DESC, created_at DESC, id DESC
        LIMIT 20
        """
    ).fetchall()
    proposals = context.conn.execute(
        """
        SELECT proposal_id, proposed_name, status, proposed_actions, proposed_at
        FROM scene_proposals
        ORDER BY proposed_at DESC, id DESC
        LIMIT 10
        """
    ).fetchall()
    if not rows and not proposals:
        return "Hiện chưa có scene nào trong hệ thống."
    lines = ["Scene hiện có:"]
    for row in rows:
        status = "đang bật" if row["enabled"] else "đã tắt"
        last = f", lần chạy gần nhất {row['last_executed_at']}" if row["last_executed_at"] else ""
        lines.append(f"- {row['name']} ({row['scene_id']}): {status}, đã chạy {row['execution_count']} lần{last}")
    for row in proposals:
        try:
            action_count = len(json.loads(row["proposed_actions"] or "[]"))
        except json.JSONDecodeError:
            action_count = 0
        lines.append(f"- Nháp {row['proposed_name']} ({row['proposal_id']}): {row['status']}, {action_count} hành động")
    return "\n".join(lines)


def _format_scene_query(context: SkillContext, scene_name: str | None) -> str:
    if not scene_name:
        return _format_scene_list(context)
    normalized = normalize_text(scene_name)
    rows = context.conn.execute(
        """
        SELECT scene_id, name, enabled, execution_count, last_executed_at
        FROM scenes
        ORDER BY updated_at DESC, created_at DESC, id DESC
        """
    ).fetchall()
    matches = [row for row in rows if normalized in normalize_text(row["name"]) or normalized == normalize_text(row["scene_id"])]
    if not matches:
        return f"Không tìm thấy scene phù hợp với '{scene_name}'."
    if len(matches) > 1:
        names = ", ".join(row["name"] for row in matches[:5])
        return f"Tìm thấy nhiều scene phù hợp: {names}. Bạn nói rõ tên scene hơn nhé."
    row = matches[0]
    status = "đang bật" if row["enabled"] else "đã tắt"
    last = row["last_executed_at"] or "chưa chạy lần nào"
    return f"Scene {row['name']} ({row['scene_id']}): {status}, đã chạy {row['execution_count']} lần, lần gần nhất: {last}."
