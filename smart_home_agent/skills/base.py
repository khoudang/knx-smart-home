from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Protocol

from smart_home_agent.agent_core.command_models import AdapterInput, Intent
from smart_home_agent.integrations.deepseek_client import DeepSeekIntentParser
from smart_home_agent.integrations.device_control import DeviceControlClient
from smart_home_agent.services.user_repository import UserRole


@dataclass(frozen=True)
class SkillRoute:
    intent: str
    skill: str
    confidence: float
    entities: dict = field(default_factory=dict)
    requires_tool: bool = False
    requires_confirmation: bool = False
    permission_action: str | None = None
    clarification_needed: bool = False
    parsed_intent: Intent | None = None
    fallback_reason: str | None = None


@dataclass(frozen=True)
class SkillResult:
    reply: str
    status: str
    success: bool = True
    parsed_intent: Intent | None = None
    resolved_device_ids: list[str] = field(default_factory=list)
    resolved_entities: list[str] = field(default_factory=list)
    resulting_state: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    sensitive: bool = False
    executed: bool = False
    notifications: list[dict] = field(default_factory=list)
    command_execution: dict | None = None


@dataclass
class SkillContext:
    conn: sqlite3.Connection
    adapter_input: AdapterInput
    user_role: UserRole
    intent_parser: DeepSeekIntentParser
    device_client: DeviceControlClient
    block_legacy_admin_writes: bool = False


class Skill(Protocol):
    name: str
    supported_intents: tuple[str, ...]
    examples: tuple[str, ...]
    required_role: str
    requires_confirmation: bool

    def match(self, context: SkillContext) -> SkillRoute | None:
        ...

    def extract_entities(self, context: SkillContext, route: SkillRoute) -> dict:
        ...

    def validate(self, context: SkillContext, route: SkillRoute) -> SkillRoute:
        ...

    def execute(self, context: SkillContext, route: SkillRoute) -> SkillResult:
        ...

    def render_response(self, context: SkillContext, route: SkillRoute, result: SkillResult) -> str:
        ...


class BaseSkill:
    name = "base"
    supported_intents: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    required_role = "member"
    requires_confirmation = False

    def extract_entities(self, context: SkillContext, route: SkillRoute) -> dict:
        return route.entities

    def validate(self, context: SkillContext, route: SkillRoute) -> SkillRoute:
        return route

    def render_response(self, context: SkillContext, route: SkillRoute, result: SkillResult) -> str:
        return result.reply
