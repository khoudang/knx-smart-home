from __future__ import annotations

import json
import os
from typing import Protocol

from smart_home_agent.agent_core.command_models import Intent


class IntentParser(Protocol):
    def parse(self, text: str) -> Intent:
        ...

    def chat(self, text: str) -> str:
        ...

    def weather_summary(self, question: str, weather_report: dict) -> str:
        ...


class DeepSeekIntentParser:
    def __init__(self, api_key: str | None, model: str = "deepseek-chat") -> None:
        self.api_key = api_key
        self.model = model

    @classmethod
    def from_env(cls) -> "DeepSeekIntentParser":
        return cls(os.environ.get("DEEPSEEK_API_KEY"))

    def parse(self, text: str) -> Intent:
        if not self.api_key:
            raise ValueError("DeepSeek API key is not configured and local parser was not confident")
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url="https://api.deepseek.com/v1")
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Return only compact JSON with keys: intent, device_name, room, "
                        "parameters, confidence. Allowed intent values are turn_on, "
                        "turn_off, set_brightness, set_color, read_state, count_devices, "
                        "list_devices, status_summary, device_info, greeting, run_scene, create_scene. "
                        "For Vietnamese smart-home commands, choose the closest allowed "
                        "intent. Use count_devices/list_devices/status_summary for inventory "
                        "questions; do not invent a device name like all devices. Do not return unknown, "
                        "ha_entity_id, KNX group addresses, API calls, tools, or "
                        "executable instructions."
                    ),
                },
                {"role": "user", "content": text},
            ],
            timeout=10,
        )
        content = response.choices[0].message.content or ""
        data = json.loads(_extract_json_object(content))
        forbidden = {"ha_entity_id", "group_address", "control_ga", "status_ga"}
        if forbidden.intersection(data):
            raise ValueError("LLM output contains forbidden execution identifiers")
        return Intent.from_dict(data)

    def chat(self, text: str) -> str:
        if not self.api_key:
            raise ValueError("DeepSeek API key is not configured")
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url="https://api.deepseek.com/v1")
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Bạn là trợ lý nhà thông minh nói tiếng Việt có dấu, trả lời ngắn gọn. "
                        "Không bịa trạng thái thiết bị, không tự thực hiện công cụ/lệnh, "
                        "không coi mọi câu là lệnh nhà thông minh, không tiết lộ secret. "
                        "Nếu câu hỏi cần dữ liệu thời gian thực hoặc công cụ chưa có, hãy nói rõ giới hạn."
                    ),
                },
                {"role": "user", "content": text},
            ],
            timeout=10,
        )
        return (response.choices[0].message.content or "").strip()

    def weather_summary(self, question: str, weather_report: dict) -> str:
        if not self.api_key:
            raise ValueError("DeepSeek API key is not configured")
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url="https://api.deepseek.com/v1")
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Bạn diễn đạt dữ liệu thời tiết thành tiếng Việt ngắn gọn. "
                        "Chỉ dùng số liệu trong JSON được cung cấp; không bịa, không suy đoán, "
                        "không gọi công cụ, không thêm cảnh báo không có trong dữ liệu. "
                        "Nếu thiếu dữ liệu cho câu hỏi, nói rõ là chưa có dữ liệu đủ."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"question": question, "weather_report": weather_report},
                        ensure_ascii=False,
                    ),
                },
            ],
            timeout=10,
        )
        return (response.choices[0].message.content or "").strip()


def _extract_json_object(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = [line for line in stripped.splitlines() if not line.strip().startswith("```")]
        stripped = "\n".join(lines).strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM output is not JSON")
    return stripped[start : end + 1]
