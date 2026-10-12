from __future__ import annotations

from smart_home_agent.skills.base import SkillContext, SkillResult, SkillRoute
from smart_home_agent.skills.registry import SkillRegistry


class SkillRouter:
    def __init__(self, registry: SkillRegistry, min_confidence: float = 0.55) -> None:
        self.registry = registry
        self.min_confidence = min_confidence

    def route(self, context: SkillContext) -> SkillRoute:
        best: SkillRoute | None = None
        for skill in self.registry.ordered():
            route = skill.match(context)
            if not route:
                continue
            if route.confidence >= self.min_confidence:
                return route
            if best is None or route.confidence > best.confidence:
                best = route
        if best:
            return SkillRoute(
                intent=best.intent,
                skill=best.skill,
                confidence=best.confidence,
                entities=best.entities,
                clarification_needed=True,
                fallback_reason="low_confidence",
            )
        return SkillRoute(
            intent="clarification",
            skill="clarification",
            confidence=0.0,
            clarification_needed=True,
            fallback_reason="no_skill_matched",
        )

    def execute(self, context: SkillContext) -> tuple[SkillRoute, SkillResult]:
        route = self.route(context)
        if route.clarification_needed:
            result = SkillResult(
                reply="Mình chưa hiểu rõ ý bạn. Bạn có thể nói cụ thể hơn không?",
                status="clarification",
                parsed_intent=None,
                resulting_state="Mình chưa hiểu rõ ý bạn. Bạn có thể nói cụ thể hơn không?",
            )
            return route, result
        for skill in self.registry.ordered():
            if skill.name != route.skill:
                continue
            validated = skill.validate(context, route)
            return validated, skill.execute(context, validated)
        result = SkillResult(
            reply="Mình chưa được kết nối kỹ năng này.",
            status="skill_unavailable",
            success=False,
            error_code="skill_unavailable",
            error_message=f"missing skill: {route.skill}",
        )
        return route, result
