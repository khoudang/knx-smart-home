from __future__ import annotations

import json
import os
from typing import Protocol

from smart_home_agent.agent_core.intent_schema import IntentClassification, validate_classification_payload
from smart_home_agent.integrations.deepseek_client import _extract_json_object


CLASSIFIER_SYSTEM_PROMPT = """
You classify Vietnamese smart-home chatbot messages.
Return only compact JSON with keys:
domain, intent, entities, confidence, needs_clarification, clarification_question.

Allowed domains and intents:
- access_control: request_access, approve_access, reject_access, grant_permission, revoke_permission,
  revoke_all_permissions, block_user, unblock_user, remove_user, list_users,
  list_blocked_users, list_pending_requests, check_user_permission
- device_control: set_device_state, set_device_value, query_device_state,
  query_room_state, query_home_state
- scene: activate_scene, deactivate_scene, list_scenes, query_scene
- weather: get_current_weather, get_weather_forecast
- home_status: health_check, status_summary
- web_search: search_information
- general_chat: chat
- unknown: unknown

Entity rules:
- For users, put the natural target text in entities.target_user_query.
- For devices, put the natural device text in entities.device_query.
- For rooms, put the natural room text in entities.room_query.
- For device_control, use entities.device_name, device_type, room_name, state,
  value, unit, scope. Never output device IDs, group addresses, MQTT topics, or
  invented capabilities. Allowed state values are on, off, open, closed, lock,
  unlock, toggle.
- For scene, use entities.scene_name, scene_action, room_name. Never output scene
  IDs unless the user explicitly typed an existing ID.
- For weather, use entities.location as text only, date_reference as one of
  now, today, tomorrow, specific_date, next_days, unspecified; forecast_days as
  an integer when the user asks for multiple days; weather_attribute as one of
  temperature, rain, humidity, wind, general.
- For web_search, put the external lookup text in entities.query; preferred_source
  is wikipedia, web, or unspecified; freshness is current, recent, historical, or
  unspecified.
- Do not output IDs, tokens, SQL, MQTT topics, KNX group addresses, tools, or code.
- If the message is an access-control request with words like chan, mo chan,
  xoa khoi nha, cap quyen, thu hoi quyen, prefer access_control over device_control.
- Weather and web_search are read-only. Do not answer the weather/search content
  in this JSON; only classify and extract entities.
- Distinguish general_chat from web_search: use web_search when the user asks to
  search, look up, check current/recent information, Wikipedia, internet, or latest
  news. A plain knowledge question can remain general_chat.
- If confidence is below 0.55 or the user must choose among options, set
  needs_clarification true with a Vietnamese clarification question.
""".strip()


class IntentClassifier(Protocol):
    def classify(self, text: str) -> IntentClassification:
        ...


class DeepSeekIntentClassifier:
    def __init__(self, api_key: str | None, model: str = "deepseek-chat", timeout: int = 8) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "DeepSeekIntentClassifier":
        return cls(os.environ.get("DEEPSEEK_API_KEY"))

    def classify(self, text: str) -> IntentClassification:
        if not self.api_key:
            raise ValueError("DeepSeek API key is not configured")

        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url="https://api.deepseek.com/v1")
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            timeout=self.timeout,
        )
        content = response.choices[0].message.content or ""
        payload = json.loads(_extract_json_object(content))
        return validate_classification_payload(payload)
