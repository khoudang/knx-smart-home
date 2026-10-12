from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from smart_home_agent.agent_core.intent_schema import IntentClassification
from smart_home_agent.agent_core.command_models import Intent
from smart_home_agent.integrations.open_meteo import LocationNotFound, OpenMeteoClient, OpenMeteoError
from smart_home_agent.skills.base import BaseSkill, SkillContext, SkillResult, SkillRoute
from smart_home_agent.skills.common import is_weather_query, normalize_text, repair_mojibake


_CLIENT = OpenMeteoClient()


class WeatherSkill(BaseSkill):
    name = "external.weather"
    supported_intents = ("weather",)
    examples = ("thời tiết Gò Vấp hôm nay", "chiều nay có mưa không", "nhiệt độ TP.HCM")
    required_role = "viewer"

    def match(self, context: SkillContext) -> SkillRoute | None:
        if not is_weather_query(context.adapter_input.text):
            return None
        location = _extract_location(context.adapter_input.text)
        return SkillRoute(
            intent="weather",
            skill=self.name,
            confidence=0.95,
            requires_tool=True,
            entities={"location": location, "period": _period(context.adapter_input.text)},
            parsed_intent=Intent(intent="weather", confidence=0.95),
            fallback_reason="missing_location" if not location else None,
        )

    def route_from_classification(self, classification: IntentClassification) -> SkillRoute:
        location = _resolve_location(classification.entities.get("location"))
        date_reference = _date_reference(classification.entities.get("date_reference"), classification.intent)
        forecast_days = _forecast_days(classification.entities.get("forecast_days"), date_reference)
        weather_attribute = _weather_attribute(classification.entities.get("weather_attribute"))
        period = _period_from_date_reference(date_reference, forecast_days)
        return SkillRoute(
            intent=classification.intent,
            skill=self.name,
            confidence=classification.confidence,
            requires_tool=True,
            entities={
                "location": location,
                "period": period,
                "date_reference": date_reference,
                "forecast_days": forecast_days,
                "weather_attribute": weather_attribute,
            },
            parsed_intent=Intent(
                intent="weather",
                confidence=classification.confidence,
                parameters={
                    "date_reference": date_reference,
                    "forecast_days": forecast_days,
                    "weather_attribute": weather_attribute,
                },
            ),
            fallback_reason="missing_location" if not location else None,
        )

    def execute(self, context: SkillContext, route: SkillRoute) -> SkillResult:
        location_query = str(route.entities.get("location") or "").strip()
        if not location_query:
            reply = "Bạn muốn xem thời tiết ở khu vực nào?"
            return SkillResult(
                reply=reply,
                status="clarification",
                success=False,
                parsed_intent=route.parsed_intent,
                resulting_state=reply,
                error_code="missing_entity",
                error_message="missing weather location",
            )
        try:
            forecast_days = _forecast_days(route.entities.get("forecast_days"), route.entities.get("date_reference"))
            location, raw = _CLIENT.weather(location_query, forecast_days=forecast_days)
            report = _compact_report(location.label, route.entities.get("period") or "today", raw)
        except LocationNotFound:
            reply = f"Mình chưa tìm thấy địa điểm '{location_query}'. Bạn thử ghi rõ quận/thành phố hơn nhé."
            return SkillResult(
                reply=reply,
                status="weather_location_not_found",
                success=False,
                parsed_intent=route.parsed_intent,
                resulting_state=reply,
                error_code="location_not_found",
                error_message=location_query,
            )
        except OpenMeteoError as exc:
            reply = "Mình chưa lấy được dữ liệu thời tiết lúc này. Bạn thử lại sau ít phút nhé."
            return SkillResult(
                reply=reply,
                status="weather_unavailable",
                success=False,
                parsed_intent=route.parsed_intent,
                resulting_state=reply,
                error_code="external_service_unavailable",
                error_message=str(exc),
            )

        reply = _render_with_deepseek(context, report)
        return SkillResult(
            reply=reply,
            status="weather",
            parsed_intent=route.parsed_intent,
            resulting_state=reply,
            resolved_entities=[
                f"weather:{location_query}",
                f"date_reference:{route.entities.get('date_reference') or 'today'}",
                f"forecast_days:{route.entities.get('forecast_days') or 1}",
                f"attribute:{route.entities.get('weather_attribute') or 'general'}",
            ],
        )


def _extract_location(text: str) -> str | None:
    original = repair_mojibake(text).strip()
    normalized = normalize_text(original)
    aliases = {
        "tp.hcm": "TP.HCM",
        "tphcm": "TP.HCM",
        "tp hcm": "TP.HCM",
        "hcm": "TP.HCM",
        "ho chi minh": "TP.HCM",
        "sai gon": "TP.HCM",
        "quan 2": "Ho Chi Minh City",
        "q2": "Ho Chi Minh City",
        "thu duc": "Ho Chi Minh City",
    }
    for key, value in aliases.items():
        if key in normalized:
            return value
    stop_words = (
        "hom nay",
        "ngay mai",
        "chieu nay",
        "sang nay",
        "toi nay",
        "co mua khong",
        "mua khong",
        "nang nong",
        "troi nong",
        "bao nhieu do",
    )
    location = _strip_personal_clause(normalized)
    for prefix in ("thoi tiet", "du bao thoi tiet", "nhiet do"):
        if location.startswith(prefix):
            location = location[len(prefix) :].strip()
    for stop in stop_words:
        location = location.replace(stop, " ")
    location = " ".join(location.split())
    if not location or location in {"co", "mua", "khong", "du bao", "nang", "nong", "troi"}:
        return None
    if len(location) < 3:
        return None
    return location


def _strip_personal_clause(normalized: str) -> str:
    location = normalized
    for separator in (",", " - ", " nen ", " vi ", " de "):
        if separator in location:
            location = location.split(separator, 1)[0].strip()
    for marker in (" toi ", " minh ", " em ", " anh ", " chi "):
        if marker in f" {location} ":
            location = f" {location} ".split(marker, 1)[0].strip()
    return location


def _period(text: str) -> str:
    normalized = normalize_text(text)
    if "ngay mai" in normalized:
        return "tomorrow"
    if "chieu nay" in normalized:
        return "this_afternoon"
    return "today"


def _resolve_location(value: Any) -> str | None:
    direct = str(value or "").strip()
    if direct and direct.lower() not in {"none", "null", "unspecified"}:
        return direct
    for key in ("WEATHER_DEFAULT_LOCATION", "HOME_LOCATION", "DEFAULT_LOCATION"):
        configured = os.environ.get(key)
        if configured and configured.strip():
            return configured.strip()
    return None


def _date_reference(value: Any, intent: str) -> str:
    normalized = str(value or "").strip().lower()
    allowed = {"now", "today", "tomorrow", "specific_date", "next_days", "unspecified"}
    if normalized in allowed:
        return normalized
    if intent == "get_current_weather":
        return "now"
    return "today"


def _forecast_days(value: Any, date_reference: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = 1
    if str(date_reference or "").strip().lower() == "next_days":
        return max(2, min(days, 7))
    if str(date_reference or "").strip().lower() == "tomorrow":
        return max(2, days)
    return max(1, min(days, 7))


def _weather_attribute(value: Any) -> str:
    normalized = str(value or "general").strip().lower()
    return normalized if normalized in {"temperature", "rain", "humidity", "wind", "general"} else "general"


def _period_from_date_reference(date_reference: str, forecast_days: int) -> str:
    if date_reference == "tomorrow":
        return "tomorrow"
    if date_reference == "next_days" and forecast_days > 1:
        return "next_days"
    return "today"


def _compact_report(location_label: str, period: str, raw: dict[str, Any]) -> dict[str, Any]:
    current = raw.get("current") or {}
    hourly = raw.get("hourly") or {}
    daily = raw.get("daily") or {}
    selected_hours = _select_hours(hourly, period)
    today_index = 1 if period == "tomorrow" and len(daily.get("time") or []) > 1 else 0
    report = {
        "location": location_label,
        "timezone": raw.get("timezone") or "Asia/Ho_Chi_Minh",
        "period": period,
        "current": {
            "time": current.get("time"),
            "temperature_c": current.get("temperature_2m"),
            "feels_like_c": current.get("apparent_temperature"),
            "humidity_percent": current.get("relative_humidity_2m"),
            "precipitation_mm": current.get("precipitation"),
            "rain_mm": current.get("rain"),
            "wind_kmh": current.get("wind_speed_10m"),
            "weather_code": current.get("weather_code"),
        },
        "daily": _daily_at(daily, today_index),
        "hours": selected_hours,
    }
    return report


def _select_hours(hourly: dict[str, Any], period: str) -> list[dict[str, Any]]:
    times = hourly.get("time") or []
    if not times:
        return []
    selected: list[dict[str, Any]] = []
    now_date = datetime.fromisoformat(str(times[0])).date()
    target_date = now_date
    if period == "tomorrow":
        for item in times:
            date = datetime.fromisoformat(str(item)).date()
            if date > now_date:
                target_date = date
                break
    start_hour, end_hour = (12, 18) if period == "this_afternoon" else (0, 23)
    for index, item in enumerate(times):
        dt = datetime.fromisoformat(str(item))
        if dt.date() != target_date or not (start_hour <= dt.hour <= end_hour):
            continue
        selected.append(
            {
                "time": item,
                "temperature_c": _at(hourly, "temperature_2m", index),
                "humidity_percent": _at(hourly, "relative_humidity_2m", index),
                "precipitation_probability_percent": _at(hourly, "precipitation_probability", index),
                "precipitation_mm": _at(hourly, "precipitation", index),
                "rain_mm": _at(hourly, "rain", index),
                "weather_code": _at(hourly, "weather_code", index),
            }
        )
        if len(selected) >= 8:
            break
    return selected


def _daily_at(daily: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "date": _at(daily, "time", index),
        "temperature_max_c": _at(daily, "temperature_2m_max", index),
        "temperature_min_c": _at(daily, "temperature_2m_min", index),
        "precipitation_sum_mm": _at(daily, "precipitation_sum", index),
        "precipitation_probability_max_percent": _at(daily, "precipitation_probability_max", index),
        "weather_code": _at(daily, "weather_code", index),
    }


def _at(data: dict[str, Any], key: str, index: int) -> Any:
    values = data.get(key) or []
    if index < 0 or index >= len(values):
        return None
    return values[index]


def _render_with_deepseek(context: SkillContext, report: dict[str, Any]) -> str:
    try:
        formatter = getattr(context.intent_parser, "weather_summary")
        reply = formatter(context.adapter_input.text, report)
        if reply:
            return reply
    except Exception:
        pass
    current = report["current"]
    daily = report["daily"]
    rain_prob = daily.get("precipitation_probability_max_percent")
    rain_text = f", xác suất mưa cao nhất {rain_prob}%" if rain_prob is not None else ""
    return (
        f"Thời tiết {report['location']}: hiện khoảng {current.get('temperature_c')}°C, "
        f"cảm giác {current.get('feels_like_c')}°C, độ ẩm {current.get('humidity_percent')}%. "
        f"Hôm nay khoảng {daily.get('temperature_min_c')}–{daily.get('temperature_max_c')}°C"
        f"{rain_text}."
    )
