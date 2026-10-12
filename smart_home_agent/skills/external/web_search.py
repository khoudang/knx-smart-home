from __future__ import annotations

from typing import Any

from smart_home_agent.agent_core.command_models import Intent
from smart_home_agent.agent_core.intent_schema import IntentClassification
from smart_home_agent.integrations.web_search import DuckDuckGoInstantAnswerClient, WebSearchError
from smart_home_agent.skills.base import BaseSkill, SkillContext, SkillResult, SkillRoute
from smart_home_agent.skills.common import normalize_text, repair_mojibake


_CLIENT = DuckDuckGoInstantAnswerClient()


class WebSearchSkill(BaseSkill):
    name = "external.web_search"
    supported_intents = ("web_search",)
    examples = ("tìm trên web tin KNX mới nhất", "tra cứu Open-Meteo là gì")
    required_role = "viewer"

    def match(self, context: SkillContext) -> SkillRoute | None:
        if not is_web_search_query(context.adapter_input.text):
            return None
        query = _extract_query(context.adapter_input.text)
        return SkillRoute(
            intent="web_search",
            skill=self.name,
            confidence=0.94,
            entities={"query": query},
            requires_tool=True,
            parsed_intent=Intent(intent="web_search", confidence=0.94, parameters={"query": query}),
            fallback_reason="missing_query" if not query else None,
        )

    def route_from_classification(self, classification: IntentClassification) -> SkillRoute:
        query = str(classification.entities.get("query") or "").strip()
        preferred_source = _preferred_source(classification.entities.get("preferred_source"))
        freshness = _freshness(classification.entities.get("freshness"))
        return SkillRoute(
            intent=classification.intent,
            skill=self.name,
            confidence=classification.confidence,
            entities={
                "query": query,
                "preferred_source": preferred_source,
                "freshness": freshness,
            },
            requires_tool=True,
            parsed_intent=Intent(
                intent="web_search",
                confidence=classification.confidence,
                parameters={
                    "query": query,
                    "preferred_source": preferred_source,
                    "freshness": freshness,
                },
            ),
            fallback_reason="missing_query" if not query else None,
        )

    def execute(self, context: SkillContext, route: SkillRoute) -> SkillResult:
        query = str(route.entities.get("query") or "").strip()
        if not query:
            reply = "Bạn muốn mình tìm thông tin gì trên web?"
            return SkillResult(
                reply=reply,
                status="web_search_clarification",
                success=False,
                parsed_intent=route.parsed_intent,
                resulting_state=reply,
                error_code="missing_query",
                error_message="missing web search query",
            )
        try:
            results = _CLIENT.search(query)
        except WebSearchError as exc:
            reply = "Mình chưa tìm web được lúc này. Bạn thử lại sau một chút nhé."
            return SkillResult(
                reply=reply,
                status="web_search_unavailable",
                success=False,
                parsed_intent=route.parsed_intent,
                resulting_state=reply,
                error_code="external_service_unavailable",
                error_message=str(exc),
            )
        if not results:
            reply = f"Mình chưa tìm thấy kết quả web đủ rõ cho: {query}."
            return SkillResult(
                reply=reply,
                status="web_search_no_results",
                success=False,
                parsed_intent=route.parsed_intent,
                resulting_state=reply,
                error_code="no_results",
                error_message=query,
            )
        reply = _format_results(query, results)
        return SkillResult(
            reply=reply,
            status="web_search",
            parsed_intent=route.parsed_intent,
            resulting_state=reply,
            resolved_entities=[
                f"web_search:{query}",
                f"preferred_source:{route.entities.get('preferred_source') or 'unspecified'}",
                f"freshness:{route.entities.get('freshness') or 'unspecified'}",
                f"result_count:{len(results[:3])}",
            ],
        )


def is_web_search_query(text: str) -> bool:
    normalized = normalize_text(text)
    if normalized.startswith(("tim ", "tra cuu ", "xem ")) and " tren web" in normalized:
        return True
    return any(
        phrase in normalized
        for phrase in (
            "tim tren web",
            "tim web",
            "tra cuu web",
            "tra cuu tren web",
            "search web",
            "google ",
            "tim kiem ",
            "tim ho ",
            "tim giup ",
            "tim thong tin ",
            "tra thong tin ",
            "xem tren mang",
            "tren mang noi gi",
            "internet noi gi",
        )
    )


def _extract_query(text: str) -> str | None:
    original = repair_mojibake(text).strip()
    normalized = normalize_text(original)
    if (
        normalized.startswith(("tim ", "tra cuu ", "xem "))
        and " tren web" in normalized
        and not normalized.startswith(("tim tren web", "tra cuu tren web", "xem tren web"))
    ):
        words = original.split()
        normalized_words = normalized.split()
        try:
            marker = normalized_words.index("tren")
        except ValueError:
            marker = len(words)
        start = 1 if normalized_words and normalized_words[0] in {"tim", "xem"} else 2
        query = " ".join(words[start:marker]).strip(" ?!.")
        return query or None
    prefixes = (
        "tim tren web",
        "tim web",
        "tra cuu web",
        "tra cuu tren web",
        "search web",
        "google",
        "tim kiem",
        "tim ho",
        "tim giup",
        "tim thong tin",
        "tra thong tin",
        "xem tren mang",
        "tren mang noi gi ve",
        "internet noi gi ve",
    )
    for prefix in prefixes:
        if normalized.startswith(prefix):
            words = original.split()
            prefix_len = len(prefix.split())
            query = " ".join(words[prefix_len:]).strip(" ?!.")
            return query or None
    return original or None


def _format_results(query: str, results) -> str:
    lines = [f"Mình tìm web cho: {query}"]
    for index, result in enumerate(results[:3], start=1):
        snippet = result.snippet[:220].strip()
        lines.append(f"{index}. {result.title}: {snippet}\n{result.url}")
    return "\n".join(lines)


def _preferred_source(value: Any) -> str:
    normalized = str(value or "unspecified").strip().lower()
    return normalized if normalized in {"wikipedia", "web", "unspecified"} else "unspecified"


def _freshness(value: Any) -> str:
    normalized = str(value or "unspecified").strip().lower()
    return normalized if normalized in {"current", "recent", "historical", "unspecified"} else "unspecified"
