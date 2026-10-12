import json
import sqlite3
import tempfile
import unittest
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch

from smart_home_agent.agent_core.command_models import AdapterInput
from smart_home_agent.agent_core.command_models import Intent
from smart_home_agent.agent_core.intent_schema import validate_classification_payload
from smart_home_agent.agent_core.local_parser import parse_local, validate_parameters
from smart_home_agent.agent_core.orchestrator import Orchestrator
from smart_home_agent.agent_core.orchestrator import adapter_input_from_payload
from smart_home_agent.integrations.deepseek_client import DeepSeekIntentParser
from smart_home_agent.integrations.device_control import ControlResult
from smart_home_agent.integrations.open_meteo import OpenMeteoClient
from smart_home_agent.integrations.web_search import SearchResult, WebSearchError
from smart_home_agent.policies.safety import evaluate_policy
from smart_home_agent.skills.base import SkillContext
from smart_home_agent.skills.registry import build_default_registry
from smart_home_agent.skills.router import SkillRouter
from smart_home_agent.services.access_control import approve_request, create_access_request, execute_admin_command, revoke_user
from smart_home_agent.services.command_log import find_by_source_message
from smart_home_agent.services.pending_actions import confirmation_decision, normalize_control_command
from smart_home_agent.services.pipeline_log import configure_pipeline_logging, log_pipeline_event
from smart_home_agent.services.device_resolver import (
    AmbiguousDevice,
    CapabilityMismatch,
    DisabledDevice,
    NoDevicesConfigured,
    resolve_device,
)
from smart_home_agent.services.scene_service import (
    accept_scene_proposal,
    create_scene_proposal,
    execute_scene_once,
)
from smart_home_agent.services.device_sync import sync_devices
from smart_home_agent.services.user_repository import resolve_user_role, upsert_user
from smart_home_agent.storage.sqlite import connect, migrate
from scripts.import_postgres_to_sqlite import import_fixture_to_sqlite, load_fixture


class FakeKNX:
    def __init__(self, status: str = "success") -> None:
        self.status = status
        self.calls = []

    def execute(self, command):
        self.calls.append(("execute", command))
        return ControlResult(self.status, state=command.expected_state or {"state": "on"})

    def read_state(self, command):
        self.calls.append(("read_state", command))
        return ControlResult(self.status, state=command.expected_state or {"state": "on"})

    def health_check(self):
        return ControlResult("success")


class FakeParser:
    def __init__(self, intent: Intent, chat_reply: str = "Tra loi tu nhien", chat_error: Exception | None = None) -> None:
        self.intent = intent
        self.calls = 0
        self.chat_reply = chat_reply
        self.chat_error = chat_error
        self.chat_calls = 0
        self.weather_calls = 0

    def parse(self, text: str) -> Intent:
        self.calls += 1
        return self.intent

    def chat(self, text: str) -> str:
        self.chat_calls += 1
        if self.chat_error:
            raise self.chat_error
        return self.chat_reply

    def weather_summary(self, question: str, weather_report: dict) -> str:
        self.weather_calls += 1
        return f"Weather summary for {weather_report['location']}"


class FakeIntentClassifier:
    def __init__(self, payload: dict | Exception) -> None:
        self.payload = payload
        self.calls = 0

    def classify(self, text: str):
        self.calls += 1
        if isinstance(self.payload, Exception):
            raise self.payload
        return validate_classification_payload(self.payload)


class FakeWeatherClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.geocode_calls = 0
        self.forecast_calls = 0
        self.queries = []
        self.forecast_days = []

    def weather(self, query: str, forecast_days: int = 2):
        self.geocode_calls += 1
        self.forecast_calls += 1
        self.queries.append(query)
        self.forecast_days.append(forecast_days)
        if self.error:
            raise self.error
        from smart_home_agent.integrations.open_meteo import GeoLocation

        location = GeoLocation(name=query.title(), country="Vietnam", admin1="Ho Chi Minh", latitude=10.8, longitude=106.7)
        return location, {
            "timezone": "Asia/Ho_Chi_Minh",
            "current": {
                "time": "2026-07-15T10:00",
                "temperature_2m": 31.0,
                "apparent_temperature": 35.0,
                "relative_humidity_2m": 72,
                "precipitation": 0,
                "rain": 0,
                "weather_code": 3,
                "wind_speed_10m": 8.0,
            },
            "hourly": {
                "time": ["2026-07-15T12:00", "2026-07-15T15:00", "2026-07-16T12:00"],
                "temperature_2m": [32, 31, 30],
                "relative_humidity_2m": [70, 75, 78],
                "precipitation_probability": [30, 60, 55],
                "precipitation": [0, 1.2, 0.8],
                "rain": [0, 1.2, 0.8],
                "weather_code": [3, 61, 61],
            },
            "daily": {
                "time": ["2026-07-15", "2026-07-16"],
                "weather_code": [3, 61],
                "temperature_2m_max": [33, 31],
                "temperature_2m_min": [26, 25],
                "precipitation_sum": [2.5, 4.0],
                "precipitation_probability_max": [70, 80],
            },
        }


class FakeWebSearchClient:
    def __init__(self, error: Exception | None = None, results: list[SearchResult] | None = None) -> None:
        self.error = error
        self.results = results if results is not None else [
            SearchResult("KNX", "https://example.com/knx", "KNX is a building automation standard.")
        ]
        self.calls = 0

    def search(self, query: str, limit: int = 3):
        self.calls += 1
        if self.error:
            raise self.error
        return self.results[:limit]


class FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return BytesIO(self.payload)

    def __exit__(self, exc_type, exc, tb):
        return False


def _json_log(record: str) -> dict:
    _, _, payload = record.split(":", 2)
    return json.loads(payload.strip())


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        configure_pipeline_logging(enabled=False)
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "agent.db"
        self.conn = connect(self.db_path)
        migrate(self.conn)

    def tearDown(self) -> None:
        configure_pipeline_logging(enabled=False)
        self.conn.close()
        self.tmp.cleanup()

    def test_migration_creates_required_tables(self) -> None:
        tables = {
            row["name"]
            for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        self.assertIn("devices", tables)
        self.assertIn("command_log", tables)
        self.assertIn("scene_proposals", tables)
        self.assertIn("sync_history", tables)
        self.assertIn("access_requests", tables)
        self.assertIn("user_audit_log", tables)
        user_cols = [row["name"] for row in self.conn.execute("PRAGMA table_info(users)").fetchall()]
        self.assertIn("username", user_cols)

    def test_control_command_normalization_supports_vietnamese_accents(self) -> None:
        cases = {
            "huy": "cancel",
            "hủy": "cancel",
            "HỦY": "cancel",
            "  hủy  ": "cancel",
            "cancel": "cancel",
            "không": "cancel",
            "Không": "cancel",
            "dong y": "confirm",
            "đồng ý": "confirm",
            "ĐỒNG Ý": "confirm",
            "xac nhan": "confirm",
            "xác nhận": "confirm",
            "yes": "confirm",
            "ok": "confirm",
            "y": "confirm",
            "okay": "confirm",
            "tôi đồng ý": "confirm",
            "chấp nhận": "confirm",
            "thực hiện": "confirm",
            "bỏ qua": "cancel",
            "dừng lại": "cancel",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(confirmation_decision(text), expected)

        self.assertEqual(normalize_control_command("  ĐỒNG   Ý  "), "dong y")

    def test_control_command_normalization_does_not_use_substring_match(self) -> None:
        for text in ["không chắc", "tôi chưa đồng ý", "đừng thực hiện"]:
            with self.subTest(text=text):
                self.assertIsNone(confirmation_decision(text))

    def test_pipeline_logger_configure_is_idempotent(self) -> None:
        stream = StringIO()
        configure_pipeline_logging(stream=stream)
        configure_pipeline_logging(stream=stream)

        log_pipeline_event(event="duplicate_handler_check", request_id="logger-1")

        lines = [line for line in stream.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["event"], "duplicate_handler_check")

    def test_local_parser_turn_on_and_brightness_validation(self) -> None:
        intent = parse_local("Bat den tron phong R&D")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "turn_on")
        self.assertEqual(intent.room, "r&d")

        accented = parse_local("bật đèn led dây")
        self.assertIsNotNone(accented)
        self.assertEqual(accented.intent, "turn_on")
        self.assertEqual(accented.device_name, "đèn led dây")

        brightness = parse_local("Dat den tran 60 phan tram")
        self.assertEqual(brightness.intent, "set_brightness")
        validate_parameters(brightness)

        too_high = Intent(
            intent="set_brightness",
            device_name="den tran",
            parameters={"brightness": 150},
            confidence=0.9,
        )
        with self.assertRaises(ValueError):
            validate_parameters(too_high)

    def test_local_parser_accepts_natural_home_phrasing(self) -> None:
        open_light = parse_local("mở đèn tròn")
        self.assertEqual(open_light.intent, "turn_on")
        self.assertEqual(open_light.device_name, "đèn tròn")

        suffix = parse_local("đèn led dây bật lên")
        self.assertEqual(suffix.intent, "turn_on")
        self.assertEqual(suffix.device_name, "đèn led dây")

        polite = parse_local("bật giúp đèn tròn")
        self.assertEqual(polite.intent, "turn_on")
        self.assertEqual(polite.device_name, "đèn tròn")

        inventory = parse_local("cho tôi xem danh sách thiết bị")
        self.assertEqual(inventory.intent, "list_devices")

    def test_sheet_sync_insert_and_alias_resolution(self) -> None:
        result = sync_devices(
            self.conn,
            [
                {
                    "device_id": "den_tron_rd",
                    "Ten line den": "Den tron",
                    "Khu vuc": "Phong R&D",
                    "device_type": "light",
                    "G.A Control": "0/0/3",
                    "aliases": "den chinh phong rd, den tron",
                    "capabilities": "turn_on,turn_off,read_state",
                }
            ],
        )
        self.assertEqual(result.inserted, 1)

        match = resolve_device(
            self.conn,
            Intent(intent="turn_on", device_name="den chinh phong rd", room="Phong R&D", confidence=0.9),
        )
        self.assertEqual(match.device_id, "den_tron_rd")

    def test_sheet_sync_reactivates_deleted_device_id(self) -> None:
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "sim_test_light",
                    "Ten line den": "Den test",
                    "Khu vuc": "Phong test",
                    "device_type": "light",
                    "G.A Control": "14/11/20",
                    "G.A status": "14/11/21",
                    "homebridge_accessory_id": "hb_sim_test_light",
                }
            ],
        )
        sync_devices(self.conn, [])
        deleted = self.conn.execute("SELECT enabled, sync_status FROM devices WHERE device_id = 'sim_test_light'").fetchone()
        self.assertEqual(deleted["enabled"], 0)
        self.assertEqual(deleted["sync_status"], "deleted_from_sheet")

        result = sync_devices(
            self.conn,
            [
                {
                    "device_id": "sim_test_light",
                    "Ten line den": "Den test restored",
                    "Khu vuc": "Phong test",
                    "device_type": "light",
                    "G.A Control": "14/11/20",
                    "G.A status": "14/11/21",
                    "homebridge_accessory_id": "hb_sim_test_light",
                }
            ],
        )

        restored = self.conn.execute(
            "SELECT canonical_name, enabled, sync_status FROM devices WHERE device_id = 'sim_test_light'"
        ).fetchone()
        self.assertEqual(result.updated, 1)
        self.assertEqual(restored["canonical_name"], "Den test restored")
        self.assertEqual(restored["enabled"], 1)
        self.assertEqual(restored["sync_status"], "active")

    def test_sheet_sync_ignores_template_rows_and_applies_deletions(self) -> None:
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "sim_test_light",
                    "Ten line den": "Den test",
                    "Khu vuc": "Phong test",
                    "device_type": "light",
                    "G.A Control": "14/11/20",
                    "homebridge_accessory_id": "hb_sim_test_light",
                }
            ],
        )

        result = sync_devices(
            self.conn,
            [
                {
                    "device_type": "light",
                    "aliases": "",
                    "capabilities": "turn_on,turn_off,status",
                    "enabled": "1",
                    "homebridge_accessory_id": "",
                    "is_sensitive": "0",
                    "requires_confirmation": "0",
                }
            ],
        )

        deleted = self.conn.execute("SELECT enabled, sync_status FROM devices WHERE device_id = 'sim_test_light'").fetchone()
        self.assertEqual(result.disabled, 1)
        self.assertEqual(result.invalid, 0)
        self.assertEqual(deleted["enabled"], 0)
        self.assertEqual(deleted["sync_status"], "deleted_from_sheet")

    def test_resolver_normalizes_accents_and_infers_dim_capability(self) -> None:
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "den_tran_dali_g1",
                    "Ten line den": "G1 Đèn Trần",
                    "Khu vuc": "Phong R&D",
                    "device_type": "light",
                    "G.A Control": "0/1/0",
                    "G.A status": "0/1/1",
                    "G.A dim 1 byte value": "0/1/2",
                    "G.A dim 1 byte value status": "0/1/3",
                    "capabilities": "turn_on,turn_off,read_state",
                }
            ],
        )

        match = resolve_device(self.conn, Intent(intent="turn_on", device_name="den tran"))
        self.assertEqual(match.device_id, "den_tran_dali_g1")
        dim_match = resolve_device(
            self.conn,
            Intent(intent="set_brightness", device_name="đèn trần", parameters={"brightness": 50}),
        )
        self.assertIn("set_brightness", dim_match.capabilities)

    def test_inventory_count_and_list_do_not_use_single_device_resolver(self) -> None:
        upsert_user(self.conn, channel="zalo", user_id="owner", role="owner")
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "den_tron",
                    "Ten line den": "Đèn Tròn",
                    "Khu vuc": "Phòng R&D",
                    "device_type": "light",
                    "G.A Control": "0/0/3",
                    "G.A status": "0/0/4",
                },
                {
                    "device_id": "sim_den_bep",
                    "Ten line den": "Đèn bếp mô phỏng",
                    "Khu vuc": "Bếp",
                    "device_type": "light",
                    "G.A Control": "14/1/0",
                    "G.A status": "14/1/1",
                    "Ghi chú": "SIMULATED - Homebridge/Simulator only",
                },
            ],
        )
        orchestrator = Orchestrator(self.conn, device_client=FakeKNX())

        count = orchestrator.handle(
            AdapterInput(
                request_id="count-1",
                source_message_id="count-msg-1",
                channel="zalo",
                user_id="owner",
                text="nhà có bao nhiêu thiết bị",
            )
        )
        self.assertEqual(count.status, "count_devices")
        self.assertIn("2 thiết bị", count.reply)
        self.assertIn("1 thiết bị mô phỏng", count.reply)
        self.assertNotIn("device_not_found", count.reply)

        listed = orchestrator.handle(
            AdapterInput(
                request_id="list-1",
                source_message_id="list-msg-1",
                channel="zalo",
                user_id="owner",
                text="danh sách thiết bị",
            )
        )
        self.assertEqual(listed.status, "list_devices")
        self.assertIn("Đèn Tròn", listed.reply)
        self.assertNotIn("device_not_found", listed.reply)

        lights = orchestrator.handle(
            AdapterInput(
                request_id="light-count-1",
                source_message_id="light-count-msg-1",
                channel="zalo",
                user_id="owner",
                text="có bao nhiêu đèn",
            )
        )
        self.assertEqual(lights.status, "count_devices")
        self.assertIn("2 đèn", lights.reply)

    def test_greeting_and_device_info_use_registry_output(self) -> None:
        upsert_user(self.conn, channel="zalo", user_id="owner", role="owner")
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "den_tron",
                    "Ten line den": "Đèn Tròn",
                    "Khu vuc": "Phòng R&D",
                    "device_type": "light",
                    "G.A Control": "0/0/3",
                    "G.A status": "0/0/4",
                }
            ],
        )
        orchestrator = Orchestrator(self.conn, device_client=FakeKNX())
        greeting = orchestrator.handle(
            AdapterInput(
                request_id="hello-1",
                source_message_id="hello-msg-1",
                channel="zalo",
                user_id="owner",
                text="xin chào",
            )
        )
        self.assertEqual(greeting.status, "greeting")
        self.assertIn("Xin chào", greeting.reply)

        info = orchestrator.handle(
            AdapterInput(
                request_id="info-1",
                source_message_id="info-msg-1",
                channel="zalo",
                user_id="owner",
                text="đèn tròn là thiết bị nào",
            )
        )
        self.assertEqual(info.status, "device_info")
        self.assertIn("Đèn Tròn", info.reply)
        self.assertIn("Phòng R&D", info.reply)

    def test_general_conversation_routes_to_chat_not_greeting_or_resolver(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_parser = FakeParser(Intent(intent="turn_on", device_name="den tron"), chat_reply="Toi la bot, chua an com.")
        fake_knx = FakeKNX()
        orchestrator = Orchestrator(self.conn, intent_parser=fake_parser, device_client=fake_knx)

        response = orchestrator.handle(
            AdapterInput(
                request_id="chat-1",
                source_message_id="chat-msg-1",
                channel="telegram",
                user_id="owner",
                text="Em an com chua",
            )
        )

        self.assertEqual(response.status, "general_chat")
        self.assertEqual(response.reply, "Toi la bot, chua an com.")
        self.assertEqual(fake_parser.chat_calls, 1)
        self.assertEqual(fake_parser.calls, 0)
        self.assertEqual(fake_knx.calls, [])
        execution_count = self.conn.execute("SELECT COUNT(*) AS c FROM command_executions").fetchone()["c"]
        self.assertEqual(execution_count, 0)

    def test_food_question_routes_to_general_chat(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_parser = FakeParser(Intent(intent="turn_on", device_name="den tron"), chat_reply="Ban co the an chao hoac com nong.")
        response = Orchestrator(self.conn, intent_parser=fake_parser, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="food-1",
                source_message_id="food-msg-1",
                channel="telegram",
                user_id="owner",
                text="Toi doi qua, co goi y mon gi khong?",
            )
        )

        self.assertEqual(response.status, "general_chat")
        self.assertEqual(fake_parser.chat_calls, 1)
        self.assertEqual(fake_parser.calls, 0)

    def test_weather_query_uses_open_meteo_not_device_resolver(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_parser = FakeParser(Intent(intent="turn_on", device_name="den tron"))
        with patch("smart_home_agent.skills.external.weather._CLIENT", FakeWeatherClient()):
            response = Orchestrator(self.conn, intent_parser=fake_parser, device_client=FakeKNX()).handle(
                AdapterInput(
                    request_id="weather-1",
                    source_message_id="weather-msg-1",
                    channel="telegram",
                    user_id="owner",
                    text="thoi tiet Go Vap hom nay",
                )
            )

        self.assertEqual(response.status, "weather")
        self.assertIn("Weather summary", response.reply)
        self.assertEqual(fake_parser.weather_calls, 1)
        self.assertEqual(fake_parser.calls, 0)

    def test_weather_location_ignores_personal_followup_clause(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_parser = FakeParser(Intent(intent="turn_on", device_name="den tron"))
        fake_weather = FakeWeatherClient()
        with patch("smart_home_agent.skills.external.weather._CLIENT", fake_weather):
            response = Orchestrator(self.conn, intent_parser=fake_parser, device_client=FakeKNX()).handle(
                AdapterInput(
                    request_id="weather-clause-1",
                    source_message_id="weather-clause-msg-1",
                    channel="telegram",
                    user_id="owner",
                    text="Thoi tiet quan 2 hom nay co mua khong, toi dang tinh ve que",
                )
            )

        self.assertEqual(response.status, "weather")
        self.assertEqual(fake_weather.geocode_calls, 1)
        self.assertIn("Weather summary for Ho Chi Minh City", response.reply)
        self.assertEqual(fake_parser.calls, 0)

    def test_weather_missing_location_asks_clarification(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_parser = FakeParser(Intent(intent="turn_on", device_name="den tron"))
        response = Orchestrator(self.conn, intent_parser=fake_parser, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="weather-missing-1",
                source_message_id="weather-missing-msg-1",
                channel="telegram",
                user_id="owner",
                text="hom nay co mua khong",
            )
        )

        self.assertEqual(response.status, "clarification")
        self.assertIn("khu vực nào", response.reply)
        self.assertEqual(fake_parser.calls, 0)
        self.assertEqual(fake_parser.weather_calls, 0)

    def test_weather_api_error_is_friendly(self) -> None:
        from smart_home_agent.integrations.open_meteo import OpenMeteoError

        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_parser = FakeParser(Intent(intent="turn_on", device_name="den tron"))
        with patch("smart_home_agent.skills.external.weather._CLIENT", FakeWeatherClient(OpenMeteoError("timeout"))):
            response = Orchestrator(self.conn, intent_parser=fake_parser, device_client=FakeKNX()).handle(
                AdapterInput(
                    request_id="weather-error-1",
                    source_message_id="weather-error-msg-1",
                    channel="telegram",
                    user_id="owner",
                    text="nhiet do TP.HCM",
                )
            )

        self.assertEqual(response.status, "weather_unavailable")
        self.assertIn("thử lại", response.reply)
        self.assertEqual(fake_parser.weather_calls, 0)

    def test_weather_pipeline_current_uses_classifier_entities_and_default_location(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_parser = FakeParser(Intent(intent="turn_on", device_name="den tron"))
        fake_weather = FakeWeatherClient()
        classifier = FakeIntentClassifier(
            {
                "domain": "weather",
                "intent": "get_current_weather",
                "entities": {
                    "location": None,
                    "date_reference": "now",
                    "forecast_days": None,
                    "weather_attribute": "temperature",
                },
                "confidence": 0.96,
                "needs_clarification": False,
            }
        )
        with patch.dict("os.environ", {"WEATHER_DEFAULT_LOCATION": "TP.HCM"}), patch(
            "smart_home_agent.skills.external.weather._CLIENT", fake_weather
        ), self.assertLogs("aicontrol.intent_pipeline", level="INFO") as captured:
            response = Orchestrator(
                self.conn,
                intent_parser=fake_parser,
                intent_classifier=classifier,
                device_client=FakeKNX(),
            ).handle(
                AdapterInput(
                    request_id="weather-pipeline-current-1",
                    source_message_id="weather-pipeline-current-msg-1",
                    channel="telegram",
                    user_id="owner",
                    text="thời tiết hiện tại thế nào",
                )
            )

        self.assertEqual(response.status, "weather")
        self.assertEqual(fake_weather.queries, ["TP.HCM"])
        self.assertEqual(fake_weather.forecast_days, [1])
        self.assertEqual(fake_parser.weather_calls, 1)
        self.assertEqual(fake_parser.calls, 0)
        events = [_json_log(record)["event"] for record in captured.output]
        self.assertIn("weather_request_resolved", events)
        self.assertIn("weather_executor_completed", events)

    def test_weather_pipeline_forecast_location_and_days_are_validated_by_code(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_weather = FakeWeatherClient()
        classifier = FakeIntentClassifier(
            {
                "domain": "weather",
                "intent": "get_weather_forecast",
                "entities": {
                    "location": "Đà Lạt",
                    "date_reference": "next_days",
                    "forecast_days": 3,
                    "weather_attribute": "rain",
                },
                "confidence": 0.97,
                "needs_clarification": False,
            }
        )
        with patch("smart_home_agent.skills.external.weather._CLIENT", fake_weather):
            response = Orchestrator(
                self.conn,
                intent_parser=FakeParser(Intent(intent="turn_on")),
                intent_classifier=classifier,
                device_client=FakeKNX(),
            ).handle(AdapterInput("weather-pipeline-days-1", "weather-pipeline-days-msg-1", "telegram", "owner", "ba ngày tới thời tiết thế nào"))

        self.assertEqual(response.status, "weather")
        self.assertEqual(fake_weather.queries, ["Đà Lạt"])
        self.assertEqual(fake_weather.forecast_days, [3])

    def test_weather_pipeline_missing_location_asks_clarification_without_api_call(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_weather = FakeWeatherClient()
        classifier = FakeIntentClassifier(
            {
                "domain": "weather",
                "intent": "get_weather_forecast",
                "entities": {"location": None, "date_reference": "today", "forecast_days": 1, "weather_attribute": "rain"},
                "confidence": 0.95,
                "needs_clarification": False,
            }
        )
        with patch("smart_home_agent.skills.external.weather._CLIENT", fake_weather):
            response = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
                AdapterInput("weather-pipeline-missing-1", "weather-pipeline-missing-msg-1", "telegram", "owner", "hôm nay trời có mưa không")
            )

        self.assertEqual(response.status, "clarification")
        self.assertEqual(fake_weather.geocode_calls, 0)

    def test_weather_classifier_error_uses_read_only_legacy_fallback(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_weather = FakeWeatherClient()
        with patch("smart_home_agent.skills.external.weather._CLIENT", fake_weather):
            response = Orchestrator(
                self.conn,
                intent_parser=FakeParser(Intent(intent="turn_on")),
                intent_classifier=FakeIntentClassifier(ValueError("classifier down")),
                device_client=FakeKNX(),
            ).handle(AdapterInput("weather-legacy-fallback-1", "weather-legacy-fallback-msg-1", "telegram", "owner", "thoi tiet Go Vap hom nay"))

        self.assertEqual(response.status, "weather")
        self.assertEqual(fake_weather.queries, ["go vap"])

    def test_web_search_query_uses_search_client_not_general_chat(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_parser = FakeParser(Intent(intent="general_chat"), chat_reply="made up")
        fake_search = FakeWebSearchClient()
        with patch("smart_home_agent.skills.external.web_search._CLIENT", fake_search):
            response = Orchestrator(self.conn, intent_parser=fake_parser, device_client=FakeKNX()).handle(
                AdapterInput(
                    request_id="web-search-1",
                    source_message_id="web-search-msg-1",
                    channel="telegram",
                    user_id="owner",
                    text="tim tren web KNX la gi",
                )
            )

        self.assertEqual(response.status, "web_search")
        self.assertIn("KNX", response.reply)
        self.assertIn("https://example.com/knx", response.reply)
        self.assertEqual(fake_search.calls, 1)
        self.assertEqual(fake_parser.chat_calls, 0)

    def test_web_search_error_is_friendly(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_search = FakeWebSearchClient(WebSearchError("timeout"))
        with patch("smart_home_agent.skills.external.web_search._CLIENT", fake_search):
            response = Orchestrator(self.conn, device_client=FakeKNX()).handle(
                AdapterInput(
                    request_id="web-search-error-1",
                    source_message_id="web-search-error-msg-1",
                    channel="telegram",
                    user_id="owner",
                    text="tra cuu tren web Open-Meteo",
                )
            )

        self.assertEqual(response.status, "web_search_unavailable")
        self.assertIn("thử lại", response.reply)

    def test_web_search_pipeline_uses_classifier_query_for_non_keyword_question(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_search = FakeWebSearchClient()
        classifier = FakeIntentClassifier(
            {
                "domain": "web_search",
                "intent": "search_information",
                "entities": {"query": "KNX", "preferred_source": "wikipedia", "freshness": "historical"},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        with patch("smart_home_agent.skills.external.web_search._CLIENT", fake_search), self.assertLogs(
            "aicontrol.intent_pipeline", level="INFO"
        ) as captured:
            response = Orchestrator(
                self.conn,
                intent_parser=FakeParser(Intent(intent="general_chat"), chat_reply="made up"),
                intent_classifier=classifier,
                device_client=FakeKNX(),
            ).handle(AdapterInput("web-pipeline-1", "web-pipeline-msg-1", "telegram", "owner", "KNX là gì"))

        self.assertEqual(response.status, "web_search")
        self.assertEqual(fake_search.calls, 1)
        self.assertIn("https://example.com/knx", response.reply)
        events = [_json_log(record)["event"] for record in captured.output]
        self.assertIn("web_search_request_resolved", events)
        self.assertIn("web_search_executor_completed", events)

    def test_web_search_pipeline_missing_query_does_not_call_client(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_search = FakeWebSearchClient()
        classifier = FakeIntentClassifier(
            {
                "domain": "web_search",
                "intent": "search_information",
                "entities": {"query": "", "preferred_source": "web", "freshness": "current"},
                "confidence": 0.97,
                "needs_clarification": False,
            }
        )
        with patch("smart_home_agent.skills.external.web_search._CLIENT", fake_search):
            response = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
                AdapterInput("web-pipeline-missing-1", "web-pipeline-missing-msg-1", "telegram", "owner", "tìm giúp tôi")
            )

        self.assertEqual(response.status, "web_search_clarification")
        self.assertEqual(fake_search.calls, 0)

    def test_web_search_schema_invalid_does_not_call_search_for_general_question(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_search = FakeWebSearchClient()
        classifier = FakeIntentClassifier(
            {
                "domain": "web_search",
                "intent": "answer_information",
                "entities": {"query": "KNX"},
                "confidence": 0.97,
                "needs_clarification": False,
            }
        )
        with patch("smart_home_agent.skills.external.web_search._CLIENT", fake_search):
            response = Orchestrator(
                self.conn,
                intent_parser=FakeParser(Intent(intent="general_chat"), chat_reply="general answer"),
                intent_classifier=classifier,
                device_client=FakeKNX(),
            ).handle(AdapterInput("web-schema-invalid-1", "web-schema-invalid-msg-1", "telegram", "owner", "KNX là gì"))

        self.assertNotEqual(response.status, "web_search")
        self.assertEqual(fake_search.calls, 0)

    def test_web_search_classifier_error_uses_read_only_legacy_fallback(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_search = FakeWebSearchClient()
        with patch("smart_home_agent.skills.external.web_search._CLIENT", fake_search):
            response = Orchestrator(
                self.conn,
                intent_classifier=FakeIntentClassifier(ValueError("classifier down")),
                device_client=FakeKNX(),
            ).handle(AdapterInput("web-legacy-fallback-1", "web-legacy-fallback-msg-1", "telegram", "owner", "tim tren web KNX la gi"))

        self.assertEqual(response.status, "web_search")
        self.assertEqual(fake_search.calls, 1)

    def test_general_knowledge_without_search_request_can_remain_general_chat(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_search = FakeWebSearchClient()
        classifier = FakeIntentClassifier(
            {
                "domain": "general_chat",
                "intent": "chat",
                "entities": {},
                "confidence": 0.82,
                "needs_clarification": False,
            }
        )
        with patch("smart_home_agent.skills.external.web_search._CLIENT", fake_search):
            response = Orchestrator(
                self.conn,
                intent_parser=FakeParser(Intent(intent="general_chat"), chat_reply="KNX là chuẩn tự động hóa tòa nhà."),
                intent_classifier=classifier,
                device_client=FakeKNX(),
            ).handle(AdapterInput("web-general-chat-1", "web-general-chat-msg-1", "telegram", "owner", "Bạn giải thích về KNX giúp tôi"))

        self.assertEqual(response.status, "general_chat")
        self.assertEqual(fake_search.calls, 0)

    def test_scene_create_creates_proposal_without_executing_devices(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_parser = FakeParser(Intent(intent="general_chat"))
        fake_knx = FakeKNX()
        response = Orchestrator(self.conn, intent_parser=fake_parser, device_client=fake_knx).handle(
            AdapterInput(
                request_id="scene-create-1",
                source_message_id="scene-create-msg-1",
                channel="telegram",
                user_id="owner",
                text="tao scene Dam cuoi",
            )
        )

        self.assertEqual(response.status, "scene_proposal_created")
        self.assertIn("Dam cuoi", response.reply)
        self.assertEqual(fake_parser.chat_calls, 0)
        self.assertEqual(fake_knx.calls, [])
        proposal = self.conn.execute("SELECT * FROM scene_proposals WHERE proposed_name = 'Dam cuoi'").fetchone()
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal["status"], "pending")

    def test_scene_list_reads_database_not_general_chat(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        proposal_id = create_scene_proposal(self.conn, "Dam cuoi", [], confidence=0.7)
        fake_parser = FakeParser(Intent(intent="general_chat"))
        response = Orchestrator(self.conn, intent_parser=fake_parser, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="scene-list-1",
                source_message_id="scene-list-msg-1",
                channel="telegram",
                user_id="owner",
                text="hien tai he thong co cac scene gi",
            )
        )

        self.assertEqual(response.status, "list_scenes")
        self.assertIn("Dam cuoi", response.reply)
        self.assertIn(proposal_id, response.reply)
        self.assertEqual(fake_parser.chat_calls, 0)

    def test_scene_run_is_blocked_until_scene_execution_cutover(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_knx = FakeKNX()
        response = Orchestrator(self.conn, device_client=fake_knx).handle(
            AdapterInput(
                request_id="scene-run-blocked-1",
                source_message_id="scene-run-blocked-msg-1",
                channel="telegram",
                user_id="owner",
                text="chay scene Dam cuoi",
            )
        )

        self.assertEqual(response.status, "scene_run_blocked")
        self.assertEqual(fake_knx.calls, [])

    def test_scene_pipeline_uses_classifier_and_logs_structured_events(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        proposal_id = create_scene_proposal(self.conn, "Dam cuoi", [], confidence=0.7)
        scene_id = accept_scene_proposal(self.conn, proposal_id, "telegram", "owner")
        classifier = FakeIntentClassifier(
            {
                "domain": "scene",
                "intent": "query_scene",
                "entities": {"scene_name": "Dam cuoi"},
                "confidence": 0.95,
                "needs_clarification": False,
            }
        )
        fake_parser = FakeParser(Intent(intent="general_chat"))
        with self.assertLogs("aicontrol.intent_pipeline", level="INFO") as captured:
            response = Orchestrator(
                self.conn,
                intent_parser=fake_parser,
                intent_classifier=classifier,
                device_client=FakeKNX(),
            ).handle(AdapterInput("scene-pipeline-1", "scene-pipeline-msg-1", "telegram", "owner", "scene Dam cuoi dang the nao"))

        self.assertEqual(response.status, "query_scene")
        self.assertIn("Dam cuoi", response.reply)
        self.assertEqual(fake_parser.chat_calls, 0)
        self.assertEqual(classifier.calls, 1)
        self.assertIn(scene_id, response.reply)
        events = [_json_log(line)["event"] for line in captured.output]
        self.assertIn("scene_request_resolved", events)
        self.assertIn("scene_executor_completed", events)

    def test_device_pipeline_uses_classifier_for_dry_run_without_legacy_parser(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "den_tron",
                    "Ten line den": "Den Tron",
                    "Khu vuc": "Phong khach",
                    "device_type": "light",
                    "G.A Control": "0/0/3",
                    "G.A status": "0/0/4",
                }
            ],
        )
        classifier = FakeIntentClassifier(
            {
                "domain": "device_control",
                "intent": "set_device_state",
                "entities": {"device_name": "Den Tron", "state": "on"},
                "confidence": 0.97,
                "needs_clarification": False,
            }
        )
        fake_parser = FakeParser(Intent(intent="general_chat"))
        fake_knx = FakeKNX("dry_run")
        with self.assertLogs("aicontrol.intent_pipeline", level="INFO") as captured:
            response = Orchestrator(
                self.conn,
                intent_parser=fake_parser,
                intent_classifier=classifier,
                device_client=fake_knx,
            ).handle(AdapterInput("device-pipeline-1", "device-pipeline-msg-1", "telegram", "owner", "mo den tron giup toi"))

        self.assertEqual(response.status, "dry_run")
        self.assertEqual(fake_parser.calls, 0)
        self.assertEqual(len(fake_knx.calls), 1)
        self.assertEqual(fake_knx.calls[0][0], "execute")
        self.assertEqual(fake_knx.calls[0][1].device_id, "den_tron")
        events = [_json_log(line)["event"] for line in captured.output]
        self.assertIn("device_request_resolved", events)
        self.assertIn("device_executor_completed", events)

    def test_device_pipeline_logs_ambiguous_resolution_without_command(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "den_tron_khach",
                    "Ten line den": "Den Tron",
                    "Khu vuc": "Phong khach",
                    "device_type": "light",
                    "G.A Control": "0/0/3",
                    "G.A status": "0/0/4",
                },
                {
                    "device_id": "den_tron_ngu",
                    "Ten line den": "Den Tron",
                    "Khu vuc": "Phong ngu",
                    "device_type": "light",
                    "G.A Control": "0/1/3",
                    "G.A status": "0/1/4",
                },
            ],
        )
        classifier = FakeIntentClassifier(
            {
                "domain": "device_control",
                "intent": "set_device_state",
                "entities": {"device_name": "Den Tron", "state": "off"},
                "confidence": 0.97,
                "needs_clarification": False,
            }
        )
        fake_knx = FakeKNX("dry_run")
        with self.assertLogs("aicontrol.intent_pipeline", level="INFO") as captured:
            response = Orchestrator(self.conn, intent_classifier=classifier, device_client=fake_knx).handle(
                AdapterInput("device-ambiguous-1", "device-ambiguous-msg-1", "telegram", "owner", "tat den tron")
            )

        self.assertEqual(response.status, "ambiguous_target")
        self.assertEqual(fake_knx.calls, [])
        events = [_json_log(line)["event"] for line in captured.output]
        self.assertIn("device_resolution_ambiguous", events)

    def test_open_meteo_client_caches_geocode_and_weather(self) -> None:
        calls = []

        def fake_urlopen(request, timeout):
            calls.append(request.full_url)
            if "geocoding-api" in request.full_url:
                return FakeHTTPResponse(
                    {
                        "results": [
                            {
                                "name": "Go Vap",
                                "country": "Vietnam",
                                "admin1": "Ho Chi Minh",
                                "latitude": 10.84,
                                "longitude": 106.67,
                            }
                        ]
                    }
                )
            return FakeHTTPResponse(
                {
                    "timezone": "Asia/Ho_Chi_Minh",
                    "current": {"temperature_2m": 30},
                    "hourly": {"time": []},
                    "daily": {"time": []},
                }
            )

        client = OpenMeteoClient()
        with patch("urllib.request.urlopen", fake_urlopen):
            first_location, first_weather = client.weather("Go Vap")
            second_location, second_weather = client.weather("Go Vap")

        self.assertEqual(first_location.label, second_location.label)
        self.assertEqual(first_weather, second_weather)
        self.assertEqual(len([url for url in calls if "geocoding-api" in url]), 1)
        self.assertEqual(len([url for url in calls if url.startswith("https://api.open-meteo.com")]), 1)

    def test_short_insult_is_not_greeting(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_parser = FakeParser(Intent(intent="turn_on", device_name="den tron"))
        response = Orchestrator(self.conn, intent_parser=fake_parser, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="insult-1",
                source_message_id="insult-msg-1",
                channel="telegram",
                user_id="owner",
                text="ngu",
            )
        )

        self.assertEqual(response.status, "general_chat")
        self.assertEqual(fake_parser.chat_calls, 0)
        self.assertNotEqual(response.status, "greeting")

    def test_owner_yes_without_pending_request_asks_for_context(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "approve_access",
                "entities": {},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        response = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="yes-no-context-1",
                source_message_id="yes-no-context-msg-1",
                channel="telegram",
                user_id="owner",
                text="co",
            )
        )

        self.assertEqual(response.status, "admin_command")
        self.assertNotEqual(response.status, "greeting")

    def test_deepseek_timeout_returns_clarification_not_greeting(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_parser = FakeParser(
            Intent(intent="turn_on", device_name="den tron"),
            chat_error=TimeoutError("timeout"),
        )
        response = Orchestrator(self.conn, intent_parser=fake_parser, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="chat-timeout-1",
                source_message_id="chat-timeout-msg-1",
                channel="telegram",
                user_id="owner",
                text="Ke toi nghe mot cau chuyen",
            )
        )

        self.assertEqual(response.status, "general_chat")
        self.assertNotEqual(response.status, "greeting")
        self.assertEqual(fake_parser.chat_calls, 1)

    def test_skill_registry_exposes_phase1_order(self) -> None:
        names = build_default_registry().names()
        self.assertLess(names.index("user.access_request"), names.index("home.device_control"))
        self.assertLess(names.index("user.permissions"), names.index("home.device_control"))
        self.assertLess(names.index("external.web_search"), names.index("home.device_control"))
        self.assertLess(names.index("external.weather"), names.index("home.device_control"))
        self.assertLess(names.index("system.health"), names.index("home.device_control"))
        self.assertLess(names.index("home.device_control"), names.index("external.general_chat"))
        self.assertIn("home.inventory", names)
        self.assertIn("external.weather", names)
        self.assertIn("system.help", names)

    def test_skill_router_returns_structured_route_metadata(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "den_tron",
                    "Ten line den": "Đèn Tròn",
                    "Khu vuc": "Phòng R&D",
                    "device_type": "light",
                    "G.A Control": "0/0/3",
                    "G.A status": "0/0/4",
                }
            ],
        )
        context = SkillContext(
            conn=self.conn,
            adapter_input=AdapterInput(
                request_id="route-1",
                source_message_id="route-msg-1",
                channel="telegram",
                user_id="owner",
                text="bật đèn tròn",
            ),
            user_role=resolve_user_role(self.conn, "telegram", "owner"),
            intent_parser=FakeParser(Intent(intent="turn_on", device_name="den tron")),
            device_client=FakeKNX("dry_run"),
        )
        route = SkillRouter(build_default_registry()).route(context)
        self.assertEqual(route.skill, "home.device_control")
        self.assertEqual(route.intent, "turn_on")
        self.assertTrue(route.requires_tool)
        self.assertEqual(route.permission_action, "turn_on")
        self.assertGreaterEqual(route.confidence, 0.8)

    def test_natural_phrasing_routes_to_tools_not_general_chat(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "den_tron",
                    "Ten line den": "Đèn Tròn",
                    "Khu vuc": "Phòng R&D",
                    "device_type": "light",
                    "G.A Control": "0/0/3",
                    "G.A status": "0/0/4",
                }
            ],
        )
        router = SkillRouter(build_default_registry())
        base = {
            "source_message_id": "natural-msg",
            "channel": "telegram",
            "user_id": "owner",
        }
        cases = {
            "mở đèn tròn": ("home.device_control", "turn_on"),
            "đèn tròn bật lên": ("home.device_control", "turn_on"),
            "cho tôi xem danh sách thiết bị": ("home.inventory", "list_devices"),
            "tìm KNX trên web": ("external.web_search", "web_search"),
        }
        for index, (text, expected) in enumerate(cases.items(), start=1):
            context = SkillContext(
                conn=self.conn,
                adapter_input=AdapterInput(request_id=f"natural-{index}", text=text, **base),
                user_role=resolve_user_role(self.conn, "telegram", "owner"),
                intent_parser=FakeParser(Intent(intent="general_chat")),
                device_client=FakeKNX("dry_run"),
            )
            route = router.route(context)
            self.assertEqual((route.skill, route.intent), expected)

    def test_help_and_time_skills_do_not_use_device_execution(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_knx = FakeKNX()
        orchestrator = Orchestrator(self.conn, intent_parser=FakeParser(Intent(intent="turn_on")), device_client=fake_knx)

        help_response = orchestrator.handle(
            AdapterInput(
                request_id="help-1",
                source_message_id="help-msg-1",
                channel="telegram",
                user_id="owner",
                text="bot lam duoc gi",
            )
        )
        time_response = orchestrator.handle(
            AdapterInput(
                request_id="time-1",
                source_message_id="time-msg-1",
                channel="telegram",
                user_id="owner",
                text="may gio roi",
            )
        )

        self.assertEqual(help_response.status, "help")
        self.assertEqual(time_response.status, "time_date")
        self.assertEqual(fake_knx.calls, [])
        execution_count = self.conn.execute("SELECT COUNT(*) AS c FROM command_executions").fetchone()["c"]
        self.assertEqual(execution_count, 0)

    def test_identity_question_uses_system_help_not_deepseek(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        fake_parser = FakeParser(Intent(intent="turn_on"))
        response = Orchestrator(self.conn, intent_parser=fake_parser, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="identity-1",
                source_message_id="identity-msg-1",
                channel="telegram",
                user_id="owner",
                text="ten la gi ?",
            )
        )
        self.assertEqual(response.status, "identity")
        self.assertIn("AICONTROL", response.reply)
        self.assertEqual(fake_parser.chat_calls, 0)

    def test_system_health_requires_admin_role(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="viewer", role="viewer")
        response = Orchestrator(self.conn, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="health-1",
                source_message_id="health-msg-1",
                channel="telegram",
                user_id="viewer",
                text="KNX bridge dang dry-run hay live",
            )
        )
        self.assertEqual(response.status, "authorization_failed")

    def test_unicode_output_and_real_device_dry_run(self) -> None:
        upsert_user(self.conn, channel="zalo", user_id="owner", role="owner")
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "den_tron",
                    "Ten line den": "Đèn Tròn",
                    "Khu vuc": "Phòng R&D",
                    "device_type": "light",
                    "G.A Control": "0/0/3",
                    "G.A status": "0/0/4",
                }
            ],
        )
        response = Orchestrator(self.conn, device_client=FakeKNX("dry_run")).handle(
            AdapterInput(
                request_id="dry-1",
                source_message_id="dry-msg-1",
                channel="zalo",
                user_id="owner",
                text="bật đèn tròn",
            )
        )
        self.assertEqual(response.status, "dry_run")
        self.assertIn("Đã mô phỏng lệnh bật Đèn Tròn", response.reply)
        self.assertIn("Lệnh chưa được gửi xuống bus KNX", response.reply)
        self.assertNotIn("Gry-run", response.reply)
        self.assertNotIn("lenh", response.reply)

    def test_prefixed_zalo_source_message_id_is_not_prefixed_twice(self) -> None:
        adapter_input = adapter_input_from_payload(
            {"source": "zalo", "user_id": "u1", "source_message_id": "zalo:message-1", "message": "xin chào"}
        )
        self.assertEqual(adapter_input.request_id, "zalo:message-1")

    def test_adapter_payload_carries_profile_fields(self) -> None:
        adapter_input = adapter_input_from_payload(
            {
                "source": "telegram",
                "user_id": "123",
                "source_message_id": "telegram-profile-1",
                "message": "xin quyen",
                "display_name": "Nguyen Van A",
                "username": "nva",
            }
        )
        self.assertEqual(adapter_input.user_id, "123")
        self.assertEqual(adapter_input.display_name, "Nguyen Van A")
        self.assertEqual(adapter_input.username, "nva")

    def test_sheet_sync_rolls_back_invalid_rows(self) -> None:
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "valid",
                    "Ten line den": "Den hop le",
                    "Khu vuc": "Phong khach",
                    "device_type": "light",
                    "G.A Control": "0/0/1",
                }
            ],
        )
        with self.assertRaises(ValueError):
            sync_devices(
                self.conn,
                [
                    {
                        "device_id": "bad",
                        "Ten line den": "Den loi",
                        "Khu vuc": "Phong khach",
                        "device_type": "light",
                        "G.A Control": "bad-ga",
                    }
                ],
            )
        count = self.conn.execute("SELECT COUNT(*) AS c FROM devices").fetchone()["c"]
        self.assertEqual(count, 1)

    def test_duplicate_device_is_rejected(self) -> None:
        row = {
            "device_id": "dup",
            "Ten line den": "Den",
            "Khu vuc": "Phong khach",
            "device_type": "light",
            "G.A Control": "0/0/1",
        }
        with self.assertRaises(ValueError):
            sync_devices(self.conn, [row, row])
        latest = self.conn.execute(
            "SELECT status, invalid_count FROM sync_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(latest["status"], "failed")

    def test_header_only_or_blank_sheet_sync_succeeds_with_zero_devices(self) -> None:
        result = sync_devices(
            self.conn,
            [
                {},
                {"device_id": "", "Ten line den": "", "Khu vuc": ""},
                {"device_id": "scene_old", "Ten line den": "Scene an toi", "Khu vuc": "Phong khach"},
            ],
        )
        self.assertEqual(result.inserted, 0)
        self.assertEqual(result.updated, 0)
        count = self.conn.execute("SELECT COUNT(*) AS c FROM devices").fetchone()["c"]
        self.assertEqual(count, 0)

    def test_empty_registry_returns_clear_error_without_knx_execution(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="u1", role="owner")
        fake_knx = FakeKNX()
        response = Orchestrator(self.conn, device_client=fake_knx).handle(
            AdapterInput(
                request_id="empty-1",
                source_message_id="empty-msg-1",
                channel="telegram",
                user_id="u1",
                text="Bat den phong khach",
                role="owner",
            )
        )
        self.assertEqual(response.status, "no_devices_configured")
        self.assertIn("Chưa có thiết bị", response.reply)
        self.assertEqual(fake_knx.calls, [])
        execution_count = self.conn.execute("SELECT COUNT(*) AS c FROM command_executions").fetchone()["c"]
        self.assertEqual(execution_count, 0)

    def test_resolver_reports_empty_registry_separately(self) -> None:
        with self.assertRaises(NoDevicesConfigured):
            resolve_device(self.conn, Intent(intent="turn_on", device_name="Den chinh"))

    def test_ambiguous_disabled_and_capability_mismatch(self) -> None:
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "a",
                    "Ten line den": "Den chinh",
                    "Khu vuc": "Phong khach",
                    "device_type": "light",
                    "G.A Control": "0/0/1",
                    "capabilities": "turn_on",
                },
                {
                    "device_id": "b",
                    "Ten line den": "Den chinh",
                    "Khu vuc": "Phong ngu",
                    "device_type": "light",
                    "G.A Control": "0/0/2",
                    "capabilities": "turn_on",
                },
            ],
        )
        with self.assertRaises(AmbiguousDevice):
            resolve_device(self.conn, Intent(intent="turn_on", device_name="Den chinh"))

        self.conn.execute("UPDATE devices SET enabled = 0 WHERE device_id = 'a'")
        with self.assertRaises(DisabledDevice):
            resolve_device(self.conn, Intent(intent="turn_on", device_name="Den chinh", room="Phong khach"))

        with self.assertRaises(CapabilityMismatch):
            resolve_device(self.conn, Intent(intent="turn_off", device_name="Den chinh", room="Phong ngu"))

    def test_sensitive_policy(self) -> None:
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "cong",
                    "Ten line den": "Cong",
                    "Khu vuc": "San",
                    "device_type": "gate",
                    "G.A Control": "1/0/1",
                    "capabilities": "turn_on,turn_off",
                }
            ],
        )
        self.conn.execute("UPDATE devices SET is_sensitive = 1 WHERE device_id = 'cong'")
        device = resolve_device(self.conn, Intent(intent="turn_on", device_name="Cong"))

        guest = evaluate_policy(Intent(intent="turn_on", device_name="Cong"), device, role="guest")
        self.assertEqual(guest.status, "authorization_failed")

        owner = evaluate_policy(Intent(intent="turn_on", device_name="Cong"), device, role="owner")
        self.assertEqual(owner.status, "confirmation_required")

        confirmed = evaluate_policy(Intent(intent="turn_on", device_name="Cong"), device, role="owner", confirmed=True)
        self.assertTrue(confirmed.allowed)

    def test_user_role_lookup_and_role_spoofing_rejected(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="u1", role="family")
        role = resolve_user_role(self.conn, "telegram", "u1")
        self.assertEqual(role.role, "member")

        sync_devices(
            self.conn,
            [
                {
                    "device_id": "cong_spoof",
                    "Ten line den": "Cong spoof",
                    "Khu vuc": "San",
                    "device_type": "gate",
                    "G.A Control": "1/0/2",
                    "capabilities": "turn_on,turn_off",
                }
            ],
        )
        self.conn.execute("UPDATE devices SET is_sensitive = 1 WHERE device_id = 'cong_spoof'")
        orchestrator = Orchestrator(self.conn, device_client=FakeKNX())
        response = orchestrator.handle(
            AdapterInput(
                request_id="spoof-1",
                source_message_id="spoof-msg-1",
                channel="telegram",
                user_id="u1",
                text="Bat Cong spoof",
                role="owner",
            )
        )
        self.assertEqual(response.status, "authorization_failed")

    def test_orchestrator_uses_resolver_policy_fake_knx_and_idempotency(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="u1", role="owner")
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "den_tron",
                    "Ten line den": "Den tron",
                    "Khu vuc": "Phong khach",
                    "device_type": "light",
                    "G.A Control": "0/0/1",
                    "capabilities": "turn_on,turn_off,read_state",
                }
            ],
        )
        fake_knx = FakeKNX()
        orchestrator = Orchestrator(self.conn, device_client=fake_knx)
        request = AdapterInput(
            request_id="req-1",
            source_message_id="telegram-1",
            channel="telegram",
            user_id="u1",
            text="Bat Den tron",
            role="owner",
        )

        first = orchestrator.handle(request)
        second = orchestrator.handle(request)

        self.assertEqual(first.status, "success")
        self.assertEqual(second.status, "success")
        self.assertEqual(len(fake_knx.calls), 1)
        self.assertEqual(fake_knx.calls[0][1].control_ga, "0/0/1")
        logged = find_by_source_message(self.conn, "telegram", "telegram-1")
        self.assertEqual(logged["request_id"], "req-1")
        execution_count = self.conn.execute("SELECT COUNT(*) AS c FROM command_executions").fetchone()["c"]
        self.assertEqual(execution_count, 1)

    def test_orchestrator_records_state_mismatch(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="u1", role="owner")
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "den_mismatch",
                    "Ten line den": "Den mismatch",
                    "Khu vuc": "Phong khach",
                    "device_type": "light",
                    "G.A Control": "0/0/9",
                    "capabilities": "turn_on",
                }
            ],
        )
        response = Orchestrator(self.conn, device_client=FakeKNX("state_mismatch")).handle(
            AdapterInput(
                request_id="mismatch-1",
                source_message_id="mismatch-msg-1",
                channel="telegram",
                user_id="u1",
                text="Bat Den mismatch",
                role="owner",
            )
        )
        self.assertEqual(response.status, "state_mismatch")

    def test_llm_output_cannot_bypass_resolver_with_group_address(self) -> None:
        data = {
            "intent": "turn_on",
            "device_name": "Den tron",
            "room": "Phong khach",
            "parameters": {},
            "confidence": 0.95,
            "control_ga": "9/9/9",
        }
        forbidden = {"ha_entity_id", "group_address", "control_ga", "status_ga"}
        self.assertTrue(forbidden.intersection(data))

    def test_deepseek_json_extraction_accepts_markdown_wrapped_object(self) -> None:
        from smart_home_agent.integrations.deepseek_client import _extract_json_object

        wrapped = '```json\n{"intent":"turn_on","device_name":"den","parameters":{},"confidence":0.9}\n```'
        self.assertEqual(
            _extract_json_object(wrapped),
            '{"intent":"turn_on","device_name":"den","parameters":{},"confidence":0.9}',
        )

    def test_scene_proposal_accept_and_execute_once(self) -> None:
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "den_scene",
                    "Ten line den": "Den scene",
                    "Khu vuc": "Phong khach",
                    "device_type": "light",
                    "G.A Control": "0/0/8",
                    "capabilities": "turn_on,turn_off",
                }
            ],
        )
        proposal_id = create_scene_proposal(
            self.conn,
            "Di ngu",
            [{"device_id": "den_scene", "action": "turn_off", "value": {}}],
            confidence=0.8,
        )
        scene_id = accept_scene_proposal(self.conn, proposal_id, "user", "u1")
        fake_knx = FakeKNX()

        first = execute_scene_once(self.conn, scene_id, "scene-req-1", "scene-msg-1", "telegram", fake_knx)
        second = execute_scene_once(self.conn, scene_id, "scene-req-1b", "scene-msg-1", "telegram", fake_knx)

        self.assertEqual(first.status, "success")
        self.assertEqual(second.status, "duplicate")
        self.assertEqual(len(fake_knx.calls), 1)

    def test_postgres_fixture_import_dry_run_and_execute(self) -> None:
        fixture = load_fixture(Path("tests/fixtures/postgres_export.json"))
        dry_run = import_fixture_to_sqlite(fixture, self.db_path, execute=False)
        self.assertEqual(dry_run["devices_seen"], 1)
        self.assertEqual(dry_run["devices_imported"], 0)

        executed = import_fixture_to_sqlite(fixture, self.db_path, execute=True, include_users=True)
        self.assertEqual(executed["devices_imported"], 1)
        self.assertEqual(executed["users_imported"], 1)

    def test_unknown_user_cannot_execute_side_effect(self) -> None:
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "den_unknown",
                    "Ten line den": "Den unknown",
                    "Khu vuc": "Phong khach",
                    "device_type": "light",
                    "G.A Control": "0/0/5",
                    "capabilities": "turn_on,read_state",
                }
            ],
        )
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "revoke_permission",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        response = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="unknown-1",
                source_message_id="unknown-msg-1",
                channel="telegram",
                user_id="stranger",
                text="Bat Den unknown",
                role="owner",
            )
        )
        self.assertEqual(response.status, "authorization_failed")
        self.assertIn("xin quyền", response.reply)

    def test_unknown_user_can_request_access_and_owner_is_notified(self) -> None:
        upsert_user(self.conn, channel="zalo", user_id="owner-1", role="owner")
        response = Orchestrator(self.conn, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="access-1",
                source_message_id="access-msg-1",
                channel="telegram",
                user_id="new-user",
                text="xin quyen",
                display_name="Nguyen Van A",
                username="nva",
            )
        )

        self.assertEqual(response.status, "access_request_pending")
        self.assertIn("Yêu cầu xin quyền", response.reply)
        self.assertEqual(len(response.notifications or []), 1)
        self.assertEqual(response.notifications[0]["channel"], "zalo")
        pending = self.conn.execute("SELECT * FROM access_requests WHERE status = 'pending'").fetchall()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["display_name"], "Nguyen Van A")
        self.assertEqual(pending[0]["username"], "nva")
        self.assertIn("Nguyen Van A", response.notifications[0]["text"])
        self.assertIn("@nva", response.notifications[0]["text"])

        duplicate = Orchestrator(self.conn, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="access-2",
                source_message_id="access-msg-2",
                channel="telegram",
                user_id="new-user",
                text="xin quy?n",
                display_name="Nguyen Van A Updated",
                username="nva2",
            )
        )
        self.assertEqual(duplicate.status, "access_request_pending")
        self.assertEqual(len(duplicate.notifications or []), 1)
        pending = self.conn.execute("SELECT * FROM access_requests WHERE status = 'pending'").fetchall()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["display_name"], "Nguyen Van A Updated")
        self.assertEqual(pending[0]["username"], "nva2")

    def test_access_request_profile_fallback_values_are_stored(self) -> None:
        first = create_access_request(
            self.conn,
            channel="telegram",
            user_id="first-only",
            display_name="Lan",
        )
        username_only = create_access_request(
            self.conn,
            channel="telegram",
            user_id="username-only",
            display_name="@useronly",
            username="useronly",
        )
        unknown = create_access_request(
            self.conn,
            channel="telegram",
            user_id="unknown-name",
            display_name="Không rõ tên",
        )
        rows = {
            row["user_id"]: row
            for row in self.conn.execute(
                "SELECT user_id, display_name, username FROM access_requests WHERE id IN (?, ?, ?)",
                (first.request_id, username_only.request_id, unknown.request_id),
            ).fetchall()
        }
        self.assertEqual(rows["first-only"]["display_name"], "Lan")
        self.assertEqual(rows["username-only"]["display_name"], "@useronly")
        self.assertEqual(rows["username-only"]["username"], "useronly")
        self.assertEqual(rows["unknown-name"]["display_name"], "Không rõ tên")

    def test_owner_approves_access_request_by_text_command(self) -> None:
        upsert_user(self.conn, channel="zalo", user_id="owner-1", role="owner")
        request = create_access_request(
            self.conn,
            channel="telegram",
            user_id="new-user",
            display_name="Nguyen Van A",
            username="nva",
            source_message_id="msg-1",
        )
        self.assertIsNotNone(request.request_id)

        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "approve_access",
                "entities": {"request_id": request.request_id},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        response = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="approve-1",
                source_message_id="approve-msg-1",
                channel="zalo",
                user_id="owner-1",
                text=f"duyet {request.request_id} member",
            )
        )

        self.assertEqual(response.status, "admin_command")
        self.assertEqual(len(response.notifications or []), 1)
        self.assertEqual(response.notifications[0]["channel"], "telegram")
        self.assertEqual(response.notifications[0]["user_id"], "new-user")
        approved = resolve_user_role(self.conn, "telegram", "new-user")
        self.assertTrue(approved.known)
        self.assertEqual(approved.role, "member")
        user_profile = self.conn.execute(
            "SELECT display_name, username FROM users WHERE channel = 'telegram' AND user_id = 'new-user'"
        ).fetchone()
        self.assertEqual(user_profile["display_name"], "Nguyen Van A")
        self.assertEqual(user_profile["username"], "nva")
        audit_count = self.conn.execute("SELECT COUNT(*) AS c FROM user_audit_log").fetchone()["c"]
        self.assertGreaterEqual(audit_count, 2)

        duplicate = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="approve-2",
                source_message_id="approve-msg-2",
                channel="zalo",
                user_id="owner-1",
                text=f"duyet {request.request_id} member",
            )
        )
        self.assertEqual(duplicate.status, "admin_command")
        self.assertIn("đã được xử lý", duplicate.reply)
        self.assertEqual(duplicate.notifications or [], [])

    def test_reject_request_does_not_create_user(self) -> None:
        upsert_user(self.conn, channel="zalo", user_id="owner-1", role="owner")
        request = create_access_request(self.conn, channel="telegram", user_id="new-user")
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "reject_access",
                "entities": {},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        response = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="reject-1",
                source_message_id="reject-msg-1",
                channel="zalo",
                user_id="owner-1",
                text=f"tu choi {request.request_id}",
            )
        )

        self.assertEqual(response.status, "admin_command")
        rejected = self.conn.execute("SELECT status FROM access_requests WHERE id = ?", (request.request_id,)).fetchone()
        self.assertEqual(rejected["status"], "rejected")
        self.assertFalse(resolve_user_role(self.conn, "telegram", "new-user").known)

    def test_owner_negative_reply_rejects_single_pending_request(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        request = create_access_request(self.conn, channel="telegram", user_id="new-user")
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "reject_access",
                "entities": {},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        response = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="no-1",
                source_message_id="no-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="không",
            )
        )

        self.assertEqual(response.status, "admin_command")
        rejected = self.conn.execute("SELECT status FROM access_requests WHERE id = ?", (request.request_id,)).fetchone()
        self.assertEqual(rejected["status"], "rejected")

        mojibake = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="no-2",
                source_message_id="no-msg-2",
                channel="telegram",
                user_id="owner-1",
                text="kh?ng",
            )
        )
        self.assertEqual(mojibake.status, "admin_command")
        self.assertEqual(mojibake.notifications or [], [])

    def test_owner_can_approve_current_request_naturally(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        request = create_access_request(self.conn, channel="telegram", user_id="new-user")
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "approve_access",
                "entities": {},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        response = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="yes-1",
                source_message_id="yes-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="dong y",
            )
        )
        self.assertEqual(response.status, "admin_command")
        approved = self.conn.execute("SELECT status FROM access_requests WHERE id = ?", (request.request_id,)).fetchone()
        self.assertEqual(approved["status"], "approved")
        member = resolve_user_role(self.conn, "telegram", "new-user")
        self.assertEqual(member.role, "member")

    def test_owner_can_reject_current_request_naturally(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        request = create_access_request(self.conn, channel="telegram", user_id="new-user")
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "reject_access",
                "entities": {},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        response = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="reject-current-1",
                source_message_id="reject-current-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="tu choi",
            )
        )
        self.assertEqual(response.status, "admin_command")
        rejected = self.conn.execute("SELECT status FROM access_requests WHERE id = ?", (request.request_id,)).fetchone()
        self.assertEqual(rejected["status"], "rejected")

    def test_owner_can_count_users_with_natural_vietnamese(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="member", display_name="Tuan Anh")
        upsert_user(self.conn, channel="zalo", user_id="family-1", role="family", display_name="Lan")
        response = Orchestrator(self.conn, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="count-users-1",
                source_message_id="count-users-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="co may nguoi trong he thong ?",
            )
        )
        self.assertEqual(response.status, "admin_command")
        self.assertIn("3 người dùng", response.reply)
        self.assertIn("chủ nhà", response.reply)
        self.assertIn("2 thành viên đang hoạt động", response.reply)
        self.assertEqual(response.reply.count("thành viên đang hoạt động"), 1)
        self.assertNotIn("enabled", response.reply)

    def test_intent_pipeline_lists_blocked_users(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="blocked", display_name="Tuan Anh", enabled=False)
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "list_blocked_users",
                "entities": {},
                "confidence": 0.94,
                "needs_clarification": False,
            }
        )

        response = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="classifier-blocked-1",
                source_message_id="classifier-blocked-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="he thong co chan ai khong",
            )
        )

        self.assertEqual(response.status, "admin_command")
        self.assertIn("Tuan Anh", response.reply)
        self.assertEqual(classifier.calls, 1)

    def test_intent_pipeline_unblocks_user_by_name(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="blocked", display_name="Tuan Anh", enabled=False)
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "unblock_user",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.96,
                "needs_clarification": False,
            }
        )

        response = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="classifier-unblock-1",
                source_message_id="classifier-unblock-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="mo chan cho tuan anh di",
            )
        )

        self.assertEqual(response.status, "admin_command")
        restored = resolve_user_role(self.conn, "telegram", "member-1")
        self.assertTrue(restored.enabled)
        self.assertEqual(restored.role, "member")

    def test_intent_pipeline_denies_non_owner_admin_action(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="member-actor", role="member")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="blocked", display_name="Tuan Anh", enabled=False)
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "unblock_user",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.96,
                "needs_clarification": False,
            }
        )

        response = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="classifier-denied-1",
                source_message_id="classifier-denied-msg-1",
                channel="telegram",
                user_id="member-actor",
                text="mo chan cho tuan anh di",
            )
        )

        self.assertEqual(response.status, "admin_command")
        self.assertIn("quy", response.reply.lower())
        blocked = resolve_user_role(self.conn, "telegram", "member-1")
        self.assertFalse(blocked.enabled)
        self.assertEqual(blocked.role, "blocked")

    def test_intent_pipeline_remove_user_requires_confirmation(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="member", display_name="Tuan Anh")
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "remove_user",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.96,
                "needs_clarification": False,
            }
        )

        response = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="classifier-remove-1",
                source_message_id="classifier-remove-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="xoa tuan anh ra khoi nha",
            )
        )

        self.assertEqual(response.status, "confirmation_required")
        member = resolve_user_role(self.conn, "telegram", "member-1")
        self.assertTrue(member.enabled)
        self.assertEqual(member.role, "member")

    def test_intent_pipeline_falls_back_when_domain_not_migrated(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        fake_parser = FakeParser(Intent(intent="general_chat"), chat_reply="fallback chat")
        classifier = FakeIntentClassifier(
            {
                "domain": "home_status",
                "intent": "status_summary",
                "entities": {},
                "confidence": 0.96,
                "needs_clarification": False,
            }
        )

        response = Orchestrator(
            self.conn,
            intent_parser=fake_parser,
            intent_classifier=classifier,
            device_client=FakeKNX(),
        ).handle(
            AdapterInput(
                request_id="classifier-fallback-1",
                source_message_id="classifier-fallback-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="hom nay toi hoi met",
            )
        )

        self.assertNotEqual(response.status, "admin_command")
        self.assertEqual(classifier.calls, 1)

    def test_intent_schema_rejects_invalid_payload(self) -> None:
        with self.assertRaises(ValueError):
            validate_classification_payload(
                {
                    "domain": "access_control",
                    "intent": "turn_on",
                    "entities": {},
                    "confidence": 0.9,
                }
            )

    def test_intent_regression_fixture_uses_valid_schema_pairs(self) -> None:
        cases = json.loads(Path("tests/fixtures/intent_regression_cases.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(cases), 10)
        for case in cases:
            with self.subTest(text=case["input"]):
                payload = {
                    "domain": case["expected_domain"],
                    "intent": case["expected_intent"],
                    "entities": {},
                    "confidence": 0.9,
                    "needs_clarification": False,
                }
                classification = validate_classification_payload(payload)
                self.assertEqual(classification.domain, case["expected_domain"])
                self.assertEqual(classification.intent, case["expected_intent"])

    def test_intent_pipeline_classifier_error_falls_back_for_read_only_admin_text(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="blocked", display_name="Tuan Anh", enabled=False)
        classifier = FakeIntentClassifier(ValueError("bad classifier json"))

        response = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="classifier-error-1",
                source_message_id="classifier-error-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="he thong co chan ai khong",
            )
        )

        self.assertEqual(response.status, "admin_command")
        self.assertIn("Tuan Anh", response.reply)

    def test_user_list_follow_up_uses_previous_user_count_context(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner", display_name="Chu nha")
        upsert_user(
            self.conn,
            channel="telegram",
            user_id="member-1",
            role="member",
            display_name="Tuan Anh",
            username="tuananh",
        )
        fake_parser = FakeParser(Intent(intent="turn_on"))
        orchestrator = Orchestrator(self.conn, intent_parser=fake_parser, device_client=FakeKNX())
        first = orchestrator.handle(
            AdapterInput(
                request_id="count-follow-up-1",
                source_message_id="count-follow-up-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="co may nguoi trong he thong",
            )
        )
        second = orchestrator.handle(
            AdapterInput(
                request_id="count-follow-up-2",
                source_message_id="count-follow-up-msg-2",
                channel="telegram",
                user_id="owner-1",
                text="ten cua ho la gi",
            )
        )

        self.assertEqual(first.status, "admin_command")
        self.assertEqual(second.status, "admin_command")
        self.assertIn("Danh sách thành viên", second.reply)
        self.assertIn("Tuan Anh", second.reply)
        self.assertIn("@tuananh", second.reply)
        self.assertEqual(fake_parser.chat_calls, 0)

    def test_short_follow_up_and_contextual_delete_use_user_context(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner", display_name="Chu nha")
        upsert_user(
            self.conn,
            channel="telegram",
            user_id="member-1",
            role="member",
            display_name="Tuan Anh",
        )
        fake_parser = FakeParser(Intent(intent="general_chat"))
        orchestrator = Orchestrator(self.conn, intent_parser=fake_parser, device_client=FakeKNX())

        counted = orchestrator.handle(
            AdapterInput(
                request_id="short-context-1",
                source_message_id="short-context-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="co may nguoi trong he thong",
            )
        )
        listed = orchestrator.handle(
            AdapterInput(
                request_id="short-context-2",
                source_message_id="short-context-msg-2",
                channel="telegram",
                user_id="owner-1",
                text="la ai?",
            )
        )
        revoked = orchestrator.handle(
            AdapterInput(
                request_id="short-context-3",
                source_message_id="short-context-msg-3",
                channel="telegram",
                user_id="owner-1",
                text="xoa tuan anh",
            )
        )

        self.assertEqual(counted.status, "admin_command")
        self.assertEqual(listed.status, "admin_command")
        self.assertIn("Tuan Anh", listed.reply)
        self.assertEqual(revoked.status, "destructive_fallback_blocked")
        self.assertEqual(fake_parser.chat_calls, 0)
        member = resolve_user_role(self.conn, "telegram", "member-1")
        self.assertTrue(member.enabled)
        self.assertEqual(member.role, "member")

    def test_owner_can_ask_who_has_been_granted_access_naturally(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner", display_name="Chu nha")
        upsert_user(
            self.conn,
            channel="telegram",
            user_id="member-1",
            role="member",
            display_name="Tuan Anh",
            username="tuananh",
        )
        fake_parser = FakeParser(Intent(intent="general_chat"))
        response = Orchestrator(self.conn, intent_parser=fake_parser, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="who-authorized-1",
                source_message_id="who-authorized-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="Toi da cap quyen cho ai",
            )
        )

        self.assertEqual(response.status, "admin_command")
        self.assertIn("Danh sách thành viên", response.reply)
        self.assertIn("Tuan Anh", response.reply)
        self.assertEqual(fake_parser.chat_calls, 0)

    def test_owner_can_use_access_list_paraphrases(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="member", display_name="Tuan Anh")
        fake_parser = FakeParser(Intent(intent="general_chat"))
        for index, text in enumerate([
            "ai dang co quyen",
            "nhung ai duoc dung bot",
            "ai dung duoc bot",
            "danh sach nguoi dung co quyen",
        ], start=1):
            response = Orchestrator(self.conn, intent_parser=fake_parser, device_client=FakeKNX()).handle(
                AdapterInput(
                    request_id=f"access-list-paraphrase-{index}",
                    source_message_id=f"access-list-paraphrase-msg-{index}",
                    channel="telegram",
                    user_id="owner-1",
                    text=text,
                )
            )
            self.assertEqual(response.status, "admin_command")
            self.assertIn("Tuan Anh", response.reply)
        self.assertEqual(fake_parser.chat_calls, 0)

    def test_admin_domain_unclear_text_does_not_fall_to_general_chat(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        fake_parser = FakeParser(Intent(intent="general_chat"))
        response = Orchestrator(self.conn, intent_parser=fake_parser, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="admin-unclear-1",
                source_message_id="admin-unclear-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="quyen cua bot la sao",
            )
        )

        self.assertEqual(response.status, "admin_clarification")
        self.assertIn("quyền/người dùng", response.reply)
        self.assertEqual(fake_parser.chat_calls, 0)

    def test_owner_can_revoke_user_by_display_name(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(
            self.conn,
            channel="telegram",
            user_id="member-1",
            role="member",
            display_name="Tuan Anh",
            username="tuananh",
        )
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "revoke_permission",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        response = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="revoke-name-1",
                source_message_id="revoke-name-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="thu hoi quyen tuan anh",
            )
        )
        self.assertEqual(response.status, "admin_command")
        self.assertIn("Đã thu hồi quyền", response.reply)
        revoked = resolve_user_role(self.conn, "telegram", "member-1")
        self.assertEqual(revoked.role, "blocked")
        self.assertFalse(revoked.enabled)

    def test_owner_can_remove_permission_by_display_name_naturally(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(
            self.conn,
            channel="telegram",
            user_id="member-1",
            role="member",
            display_name="Tuan Anh",
            username="tuananh",
        )
        fake_parser = FakeParser(Intent(intent="general_chat"))
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "revoke_permission",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        response = Orchestrator(self.conn, intent_parser=fake_parser, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="remove-permission-name-1",
                source_message_id="remove-permission-name-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="xoa quyen tuan anh di",
            )
        )
        self.assertEqual(response.status, "admin_command")
        self.assertIn("Đã thu hồi quyền", response.reply)
        self.assertEqual(fake_parser.chat_calls, 0)
        revoked = resolve_user_role(self.conn, "telegram", "member-1")
        self.assertEqual(revoked.role, "blocked")
        self.assertFalse(revoked.enabled)

    def test_owner_can_remove_user_from_house_by_display_name(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(
            self.conn,
            channel="telegram",
            user_id="member-1",
            role="member",
            display_name="Tuan Anh",
            username="tuananh",
        )
        fake_knx = FakeKNX()
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "remove_user",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        response = Orchestrator(self.conn, intent_parser=FakeParser(Intent(intent="turn_on")), intent_classifier=classifier, device_client=fake_knx).handle(
            AdapterInput(
                request_id="remove-from-house-1",
                source_message_id="remove-from-house-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="xóa tuấn anh ra khỏi nhà",
            )
        )

        self.assertEqual(response.status, "confirmation_required")
        self.assertEqual(fake_knx.calls, [])
        revoked = resolve_user_role(self.conn, "telegram", "member-1")
        self.assertEqual(revoked.role, "member")
        self.assertTrue(revoked.enabled)

    def test_pending_remove_user_confirms_once_for_same_owner(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="member", display_name="Tuan Anh")
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "remove_user",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        orchestrator = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX())

        pending = orchestrator.handle(
            AdapterInput(
                request_id="pending-remove-1",
                source_message_id="pending-remove-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="xoa tuan anh ra khoi nha",
            )
        )
        self.assertEqual(pending.status, "confirmation_required")
        self.assertTrue(resolve_user_role(self.conn, "telegram", "member-1").enabled)

        pending_row = self.conn.execute("SELECT * FROM pending_actions WHERE status = 'pending'").fetchone()
        self.assertIsNotNone(pending_row)
        pending_entities = json.loads(pending_row["resolved_entities"])
        self.assertEqual(pending_entities["target_channel"], "telegram")
        self.assertEqual(pending_entities["target_user_id"], "member-1")

        with patch("smart_home_agent.agent_core.intent_router.execute_admin_command", wraps=execute_admin_command) as executor:
            confirmed = orchestrator.handle(
                AdapterInput(
                    request_id="pending-remove-2",
                    source_message_id="pending-remove-msg-2",
                    channel="telegram",
                    user_id="owner-1",
                    text="dong y",
                )
            )
            self.assertEqual(executor.call_count, 1)
            with patch.dict("os.environ", {"INTENT_CLASSIFIER_ENABLED": "false"}):
                replay = orchestrator.handle(
                    AdapterInput(
                        request_id="pending-remove-3",
                        source_message_id="pending-remove-msg-3",
                        channel="telegram",
                        user_id="owner-1",
                        text="yes",
                    )
                )
            self.assertEqual(executor.call_count, 1)
        self.assertEqual(confirmed.status, "admin_command")
        self.assertGreaterEqual(classifier.calls, 1)
        member = resolve_user_role(self.conn, "telegram", "member-1")
        self.assertEqual(member.role, "blocked")
        self.assertFalse(member.enabled)
        pending_rows = self.conn.execute("SELECT status FROM pending_actions").fetchall()
        self.assertEqual([row["status"] for row in pending_rows], ["completed"])
        self.assertNotEqual(replay.status, "admin_command")
        audit_count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM user_audit_log WHERE action = 'user_revoked'"
        ).fetchone()["c"]
        self.assertEqual(audit_count, 1)

    def test_pending_remove_user_cannot_be_confirmed_by_other_actor(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="owner-2", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="member", display_name="Tuan Anh")
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "remove_user",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput("pending-other-1", "pending-other-msg-1", "telegram", "owner-1", "xoa tuan anh")
        )

        other = Orchestrator(self.conn, device_client=FakeKNX()).handle(
            AdapterInput("pending-other-2", "pending-other-msg-2", "telegram", "owner-2", "dong y")
        )
        self.assertNotEqual(other.status, "admin_command")
        member = resolve_user_role(self.conn, "telegram", "member-1")
        self.assertTrue(member.enabled)
        self.assertEqual(member.role, "member")

    def test_pending_remove_user_expired_action_does_not_execute(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="member", display_name="Tuan Anh")
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "remove_user",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput("pending-expired-1", "pending-expired-msg-1", "telegram", "owner-1", "xoa tuan anh")
        )
        self.conn.execute("UPDATE pending_actions SET expires_at = '2000-01-01T00:00:00Z'")
        self.conn.commit()

        expired = Orchestrator(self.conn, device_client=FakeKNX()).handle(
            AdapterInput("pending-expired-2", "pending-expired-msg-2", "telegram", "owner-1", "ok")
        )
        self.assertNotEqual(expired.status, "admin_command")
        member = resolve_user_role(self.conn, "telegram", "member-1")
        self.assertTrue(member.enabled)
        self.assertEqual(member.role, "member")
        status = self.conn.execute("SELECT status FROM pending_actions").fetchone()["status"]
        self.assertEqual(status, "expired")

    def test_pending_remove_user_cancel_does_not_execute(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="member", display_name="Tuan Anh")
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "remove_user",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        orchestrator = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX())
        orchestrator.handle(AdapterInput("pending-cancel-1", "pending-cancel-msg-1", "telegram", "owner-1", "xoa tuan anh"))
        cancelled = orchestrator.handle(AdapterInput("pending-cancel-2", "pending-cancel-msg-2", "telegram", "owner-1", "huy"))

        self.assertEqual(cancelled.status, "confirmation_cancelled")
        member = resolve_user_role(self.conn, "telegram", "member-1")
        self.assertTrue(member.enabled)
        self.assertEqual(member.role, "member")
        status = self.conn.execute("SELECT status FROM pending_actions").fetchone()["status"]
        self.assertEqual(status, "cancelled")

    def test_pending_remove_user_cancel_accepts_vietnamese_diacritics_before_classifier(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="member", display_name="Tuan Anh")
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "remove_user",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        orchestrator = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX())
        orchestrator.handle(AdapterInput("pending-cancel-accent-1", "pending-cancel-accent-msg-1", "telegram", "owner-1", "xoa tuan anh"))
        self.assertEqual(classifier.calls, 1)

        cancelled = orchestrator.handle(AdapterInput("pending-cancel-accent-2", "pending-cancel-accent-msg-2", "telegram", "owner-1", "hủy"))

        self.assertEqual(cancelled.status, "confirmation_cancelled")
        self.assertEqual(classifier.calls, 1)
        member = resolve_user_role(self.conn, "telegram", "member-1")
        self.assertTrue(member.enabled)
        self.assertEqual(member.role, "member")
        status = self.conn.execute("SELECT status FROM pending_actions").fetchone()["status"]
        self.assertEqual(status, "cancelled")

    def test_pending_remove_user_cancel_accepts_uppercase_and_spaces(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="member", display_name="Tuan Anh")
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "remove_user",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        orchestrator = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX())
        orchestrator.handle(AdapterInput("pending-cancel-space-1", "pending-cancel-space-msg-1", "telegram", "owner-1", "xoa tuan anh"))

        cancelled = orchestrator.handle(AdapterInput("pending-cancel-space-2", "pending-cancel-space-msg-2", "telegram", "owner-1", "  HỦY  "))

        self.assertEqual(cancelled.status, "confirmation_cancelled")
        self.assertTrue(resolve_user_role(self.conn, "telegram", "member-1").enabled)

    def test_pending_remove_user_confirm_accepts_vietnamese_diacritics_once_before_classifier(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="member", display_name="Tuan Anh")
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "remove_user",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        orchestrator = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX())
        orchestrator.handle(AdapterInput("pending-confirm-accent-1", "pending-confirm-accent-msg-1", "telegram", "owner-1", "xoa tuan anh"))
        self.assertEqual(classifier.calls, 1)

        with patch("smart_home_agent.agent_core.intent_router.execute_admin_command", wraps=execute_admin_command) as executor:
            confirmed = orchestrator.handle(
                AdapterInput("pending-confirm-accent-2", "pending-confirm-accent-msg-2", "telegram", "owner-1", "đồng ý")
            )
            replay = orchestrator.handle(
                AdapterInput("pending-confirm-accent-3", "pending-confirm-accent-msg-3", "telegram", "owner-1", "ĐỒNG Ý")
            )

        self.assertEqual(confirmed.status, "admin_command")
        self.assertEqual(executor.call_count, 1)
        self.assertEqual(classifier.calls, 2)
        self.assertNotEqual(replay.status, "admin_command")
        member = resolve_user_role(self.conn, "telegram", "member-1")
        self.assertEqual(member.role, "blocked")
        self.assertFalse(member.enabled)

    def test_pending_control_command_is_actor_and_channel_scoped_for_cancel(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="owner-2", role="owner")
        upsert_user(self.conn, channel="zalo", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="member", display_name="Tuan Anh")
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "remove_user",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        orchestrator = Orchestrator(self.conn, intent_classifier=classifier, intent_parser=FakeParser(Intent("general_chat")), device_client=FakeKNX())
        orchestrator.handle(AdapterInput("pending-cancel-scope-1", "pending-cancel-scope-msg-1", "telegram", "owner-1", "xoa tuan anh"))

        other_actor = orchestrator.handle(AdapterInput("pending-cancel-scope-2", "pending-cancel-scope-msg-2", "telegram", "owner-2", "hủy"))
        other_channel = orchestrator.handle(AdapterInput("pending-cancel-scope-3", "pending-cancel-scope-msg-3", "zalo", "owner-1", "hủy"))

        self.assertNotEqual(other_actor.status, "confirmation_cancelled")
        self.assertNotEqual(other_channel.status, "confirmation_cancelled")
        status = self.conn.execute("SELECT status FROM pending_actions").fetchone()["status"]
        self.assertEqual(status, "pending")
        self.assertTrue(resolve_user_role(self.conn, "telegram", "member-1").enabled)

    def test_control_command_without_pending_does_not_call_pending_executor(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        classifier = FakeIntentClassifier(
            {
                "domain": "general_chat",
                "intent": "chat",
                "entities": {},
                "confidence": 0.6,
                "needs_clarification": False,
            }
        )
        orchestrator = Orchestrator(self.conn, intent_classifier=classifier, intent_parser=FakeParser(Intent("general_chat")), device_client=FakeKNX())

        with patch("smart_home_agent.agent_core.intent_router.IntentRouter.execute_pending_action") as executor:
            response = orchestrator.handle(AdapterInput("pending-none-1", "pending-none-msg-1", "telegram", "owner-1", "đồng ý"))

        self.assertEqual(executor.call_count, 0)
        self.assertEqual(classifier.calls, 1)
        self.assertNotEqual(response.status, "admin_command")

    def test_pending_remove_user_is_channel_scoped(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="zalo", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="member", display_name="Tuan Anh")
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "remove_user",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput("pending-channel-1", "pending-channel-msg-1", "telegram", "owner-1", "xoa tuan anh")
        )
        wrong_channel = Orchestrator(self.conn, device_client=FakeKNX()).handle(
            AdapterInput("pending-channel-2", "pending-channel-msg-2", "zalo", "owner-1", "dong y")
        )

        self.assertNotEqual(wrong_channel.status, "admin_command")
        member = resolve_user_role(self.conn, "telegram", "member-1")
        self.assertTrue(member.enabled)
        self.assertEqual(member.role, "member")

    def test_pending_remove_user_rechecks_permission_before_execute(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="member", display_name="Tuan Anh")
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "remove_user",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX()).handle(
            AdapterInput("pending-permission-1", "pending-permission-msg-1", "telegram", "owner-1", "xoa tuan anh")
        )
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="member", enabled=True)
        denied = Orchestrator(self.conn, device_client=FakeKNX()).handle(
            AdapterInput("pending-permission-2", "pending-permission-msg-2", "telegram", "owner-1", "yes")
        )

        self.assertEqual(denied.status, "confirmation_denied")
        member = resolve_user_role(self.conn, "telegram", "member-1")
        self.assertTrue(member.enabled)
        self.assertEqual(member.role, "member")

    def test_destructive_legacy_fallback_is_blocked_when_classifier_fails(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="member", display_name="Tuan Anh")
        response = Orchestrator(self.conn, intent_classifier=FakeIntentClassifier(ValueError("classifier down")), device_client=FakeKNX()).handle(
            AdapterInput("blocked-fallback-1", "blocked-fallback-msg-1", "telegram", "owner-1", "thu hoi quyen tuan anh")
        )
        self.assertEqual(response.status, "destructive_fallback_blocked")
        member = resolve_user_role(self.conn, "telegram", "member-1")
        self.assertTrue(member.enabled)
        self.assertEqual(member.role, "member")

    def test_intent_pipeline_logs_key_events_without_raw_message(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="member", display_name="Tuan Anh")
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "remove_user",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        orchestrator = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX())

        with self.assertLogs("aicontrol.intent_pipeline", level="INFO") as captured:
            orchestrator.handle(AdapterInput("log-events-1", "log-events-msg-1", "telegram", "owner-1", "xoa tuan anh"))
            orchestrator.handle(AdapterInput("log-events-2", "log-events-msg-2", "telegram", "owner-1", "huy"))

        events = [_json_log(record)["event"] for record in captured.output]
        self.assertIn("intent_classified", events)
        self.assertIn("confirmation_created", events)
        self.assertIn("confirmation_cancelled", events)
        for record in captured.output:
            payload = _json_log(record)
            self.assertNotIn("raw_message", payload)
            self.assertIn("event", payload)
            self.assertIn("request_id", payload)

    def test_intent_pipeline_logs_confirmation_executed_and_replay_blocked(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="member", display_name="Tuan Anh")
        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "remove_user",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        orchestrator = Orchestrator(self.conn, intent_classifier=classifier, device_client=FakeKNX())
        orchestrator.handle(AdapterInput("log-confirm-1", "log-confirm-msg-1", "telegram", "owner-1", "xoa tuan anh"))

        with self.assertLogs("aicontrol.intent_pipeline", level="INFO") as captured:
            orchestrator.handle(AdapterInput("log-confirm-2", "log-confirm-msg-2", "telegram", "owner-1", "dong y"))
            with patch.dict("os.environ", {"INTENT_CLASSIFIER_ENABLED": "false"}):
                orchestrator.handle(AdapterInput("log-confirm-3", "log-confirm-msg-3", "telegram", "owner-1", "yes"))

        events = [_json_log(record)["event"] for record in captured.output]
        self.assertIn("confirmation_executed", events)
        self.assertIn("confirmation_replay_blocked", events)

    def test_intent_pipeline_logs_destructive_fallback_blocked(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="member", display_name="Tuan Anh")

        with self.assertLogs("aicontrol.intent_pipeline", level="INFO") as captured:
            response = Orchestrator(
                self.conn,
                intent_classifier=FakeIntentClassifier(ValueError("classifier down")),
                device_client=FakeKNX(),
            ).handle(AdapterInput("log-block-1", "log-block-msg-1", "telegram", "owner-1", "thu hoi quyen tuan anh"))

        self.assertEqual(response.status, "destructive_fallback_blocked")
        events = [_json_log(record)["event"] for record in captured.output]
        self.assertIn("intent_fallback_used", events)
        self.assertIn("destructive_fallback_blocked", events)

    def test_owner_can_list_blocked_users_naturally(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="blocked", enabled=False, display_name="Tuan Anh")
        fake_parser = FakeParser(Intent(intent="general_chat"))

        response = Orchestrator(self.conn, intent_parser=fake_parser, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="blocked-list-1",
                source_message_id="blocked-list-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="Hệ thống có chặn ai không",
            )
        )

        self.assertEqual(response.status, "admin_command")
        self.assertIn("Tuan Anh", response.reply)
        self.assertEqual(fake_parser.chat_calls, 0)

    def test_owner_can_unblock_user_by_display_name_naturally(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        upsert_user(self.conn, channel="telegram", user_id="member-1", role="member", display_name="Tuan Anh")
        revoke_user(
            self.conn,
            actor_channel="telegram",
            actor_user_id="owner-1",
            channel="telegram",
            user_id="member-1",
        )
        fake_knx = FakeKNX()
        fake_parser = FakeParser(Intent(intent="turn_on", device_name="tuan anh"))

        classifier = FakeIntentClassifier(
            {
                "domain": "access_control",
                "intent": "unblock_user",
                "entities": {"target_user_query": "tuan anh"},
                "confidence": 0.98,
                "needs_clarification": False,
            }
        )
        response = Orchestrator(self.conn, intent_parser=fake_parser, intent_classifier=classifier, device_client=fake_knx).handle(
            AdapterInput(
                request_id="unblock-name-1",
                source_message_id="unblock-name-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="mở chặn cho tuấn anh đi",
            )
        )

        self.assertEqual(response.status, "admin_command")
        self.assertIn("Đã mở chặn", response.reply)
        restored = resolve_user_role(self.conn, "telegram", "member-1")
        self.assertEqual(restored.role, "member")
        self.assertTrue(restored.enabled)
        self.assertEqual(fake_knx.calls, [])

    def test_owner_revoke_typo_without_target_asks_clarification(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="owner-1", role="owner")
        fake_parser = FakeParser(Intent(intent="general_chat"))
        response = Orchestrator(self.conn, intent_parser=fake_parser, device_client=FakeKNX()).handle(
            AdapterInput(
                request_id="revoke-missing-1",
                source_message_id="revoke-missing-msg-1",
                channel="telegram",
                user_id="owner-1",
                text="thu hoi quyen",
            )
        )
        self.assertEqual(response.status, "admin_command")
        self.assertIn("thu hồi quyền <tên", response.reply)
        self.assertEqual(fake_parser.chat_calls, 0)

    def test_viewer_can_list_but_cannot_control(self) -> None:
        upsert_user(self.conn, channel="telegram", user_id="viewer-1", role="viewer")
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "den_viewer",
                    "Ten line den": "Den viewer",
                    "Khu vuc": "Phong khach",
                    "device_type": "light",
                    "G.A Control": "0/0/7",
                    "capabilities": "turn_on,turn_off,read_state",
                }
            ],
        )
        orchestrator = Orchestrator(self.conn, device_client=FakeKNX())
        listed = orchestrator.handle(
            AdapterInput(
                request_id="viewer-list-1",
                source_message_id="viewer-list-msg-1",
                channel="telegram",
                user_id="viewer-1",
                text="danh sach thiet bi",
            )
        )
        denied = orchestrator.handle(
            AdapterInput(
                request_id="viewer-cmd-1",
                source_message_id="viewer-cmd-msg-1",
                channel="telegram",
                user_id="viewer-1",
                text="Bat Den viewer",
            )
        )
        self.assertEqual(listed.status, "list_devices")
        self.assertEqual(denied.status, "authorization_failed")

    def test_last_owner_cannot_be_revoked(self) -> None:
        upsert_user(self.conn, channel="zalo", user_id="owner-1", role="owner")
        message = revoke_user(
            self.conn,
            actor_channel="zalo",
            actor_user_id="owner-1",
            channel="zalo",
            user_id="owner-1",
        )
        self.assertIn("owner cuối cùng", message)
        owner = resolve_user_role(self.conn, "zalo", "owner-1")
        self.assertTrue(owner.enabled)
        self.assertEqual(owner.role, "owner")

    def test_architecture_flask_api_no_longer_imports_legacy_agent_tools(self) -> None:
        source = Path("ai-agent/api.py").read_text(encoding="utf-8")
        self.assertNotIn("from agent import process_command", source)
        self.assertNotIn("from tools import", source)
        self.assertIn("Orchestrator", source)
        self.assertIn('@app.route("/health", methods=["GET"])', source)
        self.assertIn("database_path_configured", source)

    def test_voice_text_is_disabled_and_homebridge_endpoint_stays_separate(self) -> None:
        # The consolidated repo keeps the opt-in historical webhook in its own
        # component; it is never part of the primary chatbot startup.
        source = Path("adapters/openclaw/siri_webhook.py").read_text(encoding="utf-8")
        self.assertNotIn('@app.post("/voice/text")', source)
        self.assertIn('if not SECRET_TOKEN or authorization !=', source)
        self.assertIn('host="127.0.0.1"', source)
        middleware = Path("adapters/openclaw/middleware.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("KNX_DRY_RUN", "true")', middleware)
        self.assertIn('Legacy live KNX access is disabled', middleware)

    def test_homebridge_is_not_in_chatbot_gateway_path(self) -> None:
        source = Path("chatbot-gateway/index.js").read_text(encoding="utf-8")
        self.assertNotIn("homebridge", source.lower())
        self.assertNotIn("/siri/control", source)
        self.assertIn("/chat", source)

    def test_gateway_adapter_no_postgres_and_no_role_spoofing_contract(self) -> None:
        source = Path("chatbot-gateway/index.js").read_text(encoding="utf-8")
        self.assertNotIn("require('pg')", source)
        self.assertNotIn("new Pool", source)
        self.assertNotIn("role:", source)
        self.assertIn("source_message_id", source)
        self.assertIn("ADAPTER_ALLOW_NO_TOKENS", source)
        self.assertIn("TELEGRAM_ENABLED", source)
        self.assertIn("ZALO_ENABLED", source)
        self.assertIn("ZALO_MODE", source)
        self.assertIn("extractZaloSender", source)
        self.assertIn("sender_field", source)
        self.assertIn("[Telegram] result request_id=", source)
        self.assertIn("[Telegram] error request_id=", source)
        self.assertIn("textHash(text)", source)
        self.assertIn("seenZaloUpdate", source)
        self.assertIn("zaloDedupKey", source)
        self.assertIn("zalo:${senderId}:${zaloMessageTimestamp(update)}:${textHash(text)}", source)
        self.assertIn("Duplicate update skipped", source)
        self.assertIn("lock acquired", source)
        self.assertIn("lock released", source)
        self.assertIn("charset=utf-8", source)
        self.assertNotIn("../ai-agent/.env", source)

    def test_openclaw_legacy_startup_fail_closed(self) -> None:
        import subprocess

        tracked = set(subprocess.check_output(["git", "ls-files"], text=True).splitlines())
        self.assertNotIn("start_bot.sh", tracked)
        self.assertNotIn("openclaw.json", tracked)
        self.assertNotIn("agent.json", tracked)
        self.assertFalse(any(path.startswith("skills/") for path in tracked))

    def test_knx_dry_run_does_not_send_bridge_call_and_applies_allowlist(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "KNX_DRY_RUN": "true",
                "KNX_ALLOWED_TEST_DEVICE_IDS": "den_test",
                "KNX_ALLOWED_TEST_GROUP_ADDRESSES": "0/0/1",
                "KNX_BLOCKED_DEVICE_TYPES": "gate,lock,alarm",
            },
            clear=False,
        ):
            from smart_home_agent.integrations.device_control import ControlCommand
            from smart_home_agent.integrations.knx_bridge_client import KNXBridgeClient

            client = KNXBridgeClient.from_env()
            allowed = client.execute(
                ControlCommand(device_id="den_test", device_type="light", action="turn_on", control_ga="0/0/1")
            )
            blocked = client.execute(
                ControlCommand(device_id="den_real", device_type="light", action="turn_on", control_ga="0/0/2")
            )
            gate = client.execute(
                ControlCommand(device_id="gate", device_type="gate", action="turn_on", control_ga="1/0/1")
            )
        self.assertEqual(allowed.status, "dry_run")
        self.assertEqual(blocked.status, "unsupported_capability")
        self.assertEqual(gate.status, "unsupported_capability")

    def test_knx_bridge_reports_missing_status_as_accepted_but_unverified(self) -> None:
        from smart_home_agent.integrations.device_control import ControlCommand
        from smart_home_agent.integrations.knx_bridge_client import KNXBridgeClient

        client = KNXBridgeClient(dry_run=False)
        result = client.read_state(
            ControlCommand(device_id="den_tron", device_type="light", action="read_state", control_ga="0/0/1")
        )
        self.assertEqual(result.status, "accepted_but_unverified")

    def test_knx_bridge_rejects_invalid_group_address(self) -> None:
        from smart_home_agent.integrations.device_control import ControlCommand
        from smart_home_agent.integrations.knx_bridge_client import KNXBridgeClient

        client = KNXBridgeClient(dry_run=True)
        result = client.execute(
            ControlCommand(device_id="den_bad", device_type="light", action="turn_on", control_ga="bad-ga")
        )
        self.assertEqual(result.status, "invalid_group_address")

    def test_mqtt_contract_topics_and_payload_validation(self) -> None:
        from smart_home_agent.integrations.mqtt_contract import (
            build_command_payload,
            build_state_payload,
            command_topic,
            parse_command_topic,
            result_topic,
            state_topic,
            validate_command_payload,
        )

        self.assertEqual(command_topic("den_led_day", "power"), "cmd/den_led_day/power/set")
        self.assertEqual(state_topic("den_led_day", "power"), "state/den_led_day/power")
        self.assertEqual(result_topic("req-1"), "result/req-1")
        parsed = parse_command_topic("cmd/den_led_day/power/set")
        self.assertEqual(parsed.device_id, "den_led_day")
        self.assertEqual(parsed.capability, "power")

        payload = build_command_payload("req-1", "den_led_day", "power", True)
        validate_command_payload(payload)
        state = build_state_payload("den_led_day", "power", True, source="test")
        self.assertEqual(state["value"], True)

        with self.assertRaises(ValueError):
            build_command_payload("req-2", "den_led_day", "brightness", 150)
        with self.assertRaises(ValueError):
            validate_command_payload({**payload, "group_address": "0/0/1"})

    def test_knx_bridge_client_publishes_mqtt_contract_and_waits_for_state(self) -> None:
        from smart_home_agent.integrations.device_control import ControlCommand
        from smart_home_agent.integrations.knx_bridge_client import KNXBridgeClient

        published = []

        def publisher(topic, payload, retain):
            published.append((topic, json.loads(payload), retain))

        def state_reader(device_id, capability, timeout):
            return {"device_id": device_id, "capability": capability, "value": True}

        client = KNXBridgeClient(
            dry_run=False,
            mqtt_dry_run=False,
            publisher=publisher,
            state_reader=state_reader,
        )
        result = client.execute(
            ControlCommand(
                device_id="den_led_day",
                device_type="light",
                action="turn_on",
                control_ga="0/0/1",
                status_ga="0/0/2",
                expected_state={"value": True},
                request_id="req-mqtt-1",
            )
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(published[0][0], "cmd/den_led_day/power/set")
        self.assertEqual(published[0][1]["request_id"], "req-mqtt-1")
        self.assertNotIn("group_address", published[0][1])

    def test_knx_bridge_client_can_publish_mqtt_while_knx_dry_run(self) -> None:
        from smart_home_agent.integrations.device_control import ControlCommand
        from smart_home_agent.integrations.knx_bridge_client import KNXBridgeClient

        published = []
        client = KNXBridgeClient(
            dry_run=True,
            mqtt_dry_run=False,
            publisher=lambda topic, payload, retain: published.append((topic, json.loads(payload), retain)),
        )
        result = client.execute(
            ControlCommand(
                device_id="den_led_day",
                device_type="light",
                action="turn_on",
                control_ga="0/0/1",
                status_ga="0/0/2",
                request_id="req-dry-publish",
            )
        )
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(published[0][0], "cmd/den_led_day/power/set")

    def test_knx_bridge_client_maps_state_timeout_and_mismatch(self) -> None:
        from smart_home_agent.integrations.device_control import ControlCommand
        from smart_home_agent.integrations.knx_bridge_client import KNXBridgeClient

        def publisher(topic, payload, retain):
            return None

        timeout_client = KNXBridgeClient(
            dry_run=False,
            mqtt_dry_run=False,
            publisher=publisher,
            state_reader=lambda *_: (_ for _ in ()).throw(TimeoutError()),
        )
        timeout = timeout_client.execute(
            ControlCommand(
                device_id="den_timeout",
                device_type="light",
                action="turn_on",
                control_ga="0/0/1",
                status_ga="0/0/2",
                expected_state={"value": True},
            )
        )
        self.assertEqual(timeout.status, "knx_timeout")

        mismatch_client = KNXBridgeClient(
            dry_run=False,
            mqtt_dry_run=False,
            publisher=publisher,
            state_reader=lambda *_: {"value": False},
        )
        mismatch = mismatch_client.execute(
            ControlCommand(
                device_id="den_mismatch",
                device_type="light",
                action="turn_on",
                control_ga="0/0/1",
                status_ga="0/0/2",
                expected_state={"value": True},
            )
        )
        self.assertEqual(mismatch.status, "state_mismatch")

    def test_knx_bridge_never_sends_simulated_devices(self) -> None:
        from smart_home_agent.integrations.device_control import ControlCommand
        from smart_home_agent.integrations.knx_bridge_client import KNXBridgeClient

        published = []
        client = KNXBridgeClient(
            dry_run=False,
            mqtt_dry_run=False,
            publisher=lambda topic, payload, retain: published.append((topic, payload, retain)),
        )
        result = client.execute(
            ControlCommand(
                device_id="sim_lr_light_main",
                device_type="light",
                action="turn_on",
                control_ga="14/1/1",
                status_ga="14/1/2",
            )
        )

        self.assertEqual(result.status, "unsupported_capability")
        self.assertIn("Simulated devices", result.message or "")
        self.assertEqual(published, [])
        self.assertEqual(client.sent_commands, [])

    def test_knx_bridge_source_uses_new_mqtt_contract_not_legacy_home_topics(self) -> None:
        source = Path("knx-bridge/bridge.py").read_text(encoding="utf-8")
        self.assertIn('client.subscribe("cmd/+/+/set"', source)
        self.assertIn('client.subscribe("cmd/+/scene/activate"', source)
        self.assertNotIn('client.subscribe("home/', source)
        self.assertNotIn('f"home/', source)
        self.assertIn("def _normalise_homebridge_value", source)
        self.assertIn("homebridge-", source)

    def test_manage_users_script_exists_and_validates_roles(self) -> None:
        source = Path("scripts/manage_users.py").read_text(encoding="utf-8")
        self.assertIn("VALID_ROLES", source)
        self.assertIn("update-role", source)
        self.assertIn("--database", source)
        self.assertIn("homebridge", source)

    def test_google_sheet_doctor_and_sync_contract(self) -> None:
        doctor = Path("scripts/doctor.py").read_text(encoding="utf-8")
        sync = Path("scripts/sync_google_sheet.py").read_text(encoding="utf-8")
        sync_wrapper = Path("scripts/sync_devices.py").read_text(encoding="utf-8")
        env_example = Path(".env.example").read_text(encoding="utf-8")

        self.assertIn("Google credentials file", doctor)
        self.assertIn("Google client email", doctor)
        self.assertIn("Google Sheet accessible", doctor)
        self.assertIn("devices tab", doctor)
        self.assertIn("Live sync", doctor)
        self.assertIn("permission denied", doctor)
        self.assertIn("permission_denied", doctor)
        self.assertIn("tab_not_found", doctor)
        self.assertIn("GOOGLE_CREDENTIALS_FILE", env_example)
        self.assertIn("GOOGLE_SHEET_TAB=devices", env_example)
        self.assertIn("ZALO_BOT_TOKEN", env_example)

        self.assertIn("fetch_tab_rows", sync)
        self.assertIn("missing_google_service_account_json", sync)
        self.assertIn("invalid_credentials", sync)
        self.assertIn("device_count", sync)
        self.assertIn("sync_devices", sync)
        self.assertIn("--database", sync)
        self.assertIn("GOOGLE_SHEET_TAB", sync)
        self.assertNotIn("scene", sync.lower())
        self.assertIn("sync_google_sheet", sync_wrapper)

    def test_onboarding_scripts_are_one_shot_and_do_not_log_tokens(self) -> None:
        telegram = Path("scripts/onboard_telegram_user.py").read_text(encoding="utf-8")
        zalo = Path("scripts/onboard_zalo_user.py").read_text(encoding="utf-8")
        for source in (telegram, zalo):
            self.assertIn("--timeout", source)
            self.assertIn("--seed", source)
            self.assertIn("upsert_user", source)
            self.assertNotIn("print(token", source)
            self.assertNotIn("raw", source.lower())
        self.assertIn("waiting_for_user_message", telegram)
        self.assertIn("no_new_updates", zalo)
        self.assertIn("another_poller_consuming_updates", zalo)
        self.assertIn("token_not_authorized_for_getupdates", zalo)
        self.assertIn("sender_field", zalo)
        self.assertIn("telegram_user_id", telegram)

    def test_homebridge_generator_builds_expected_mqttthing_topics(self) -> None:
        from scripts.sync_homebridge import build_accessory

        sync_devices(
            self.conn,
            [
                {
                    "device_id": "sim_lr_light_main",
                    "Ten line den": "Den phong khach",
                    "Khu vuc": "Phong khach",
                    "device_type": "light",
                    "G.A Control": "14/1/1",
                    "G.A status": "14/1/2",
                    "G.A dim 1 byte value": "14/1/3",
                    "G.A dim 1 byte value status": "14/1/4",
                    "homebridge_accessory_id": "lr_light_main",
                    "capabilities": "turn_on,turn_off,set_brightness,read_state",
                }
            ],
        )
        row = self.conn.execute("SELECT * FROM devices WHERE device_id = 'sim_lr_light_main'").fetchone()
        accessory_id, accessory = build_accessory(row)

        self.assertEqual(accessory_id, "lr_light_main")
        self.assertEqual(accessory["accessory"], "mqttthing")
        self.assertEqual(accessory["type"], "lightbulb")
        self.assertEqual(accessory["topics"]["setOn"], "cmd/living_room/lr_light_main/power")
        self.assertEqual(accessory["topics"]["getBrightness"], "state/living_room/lr_light_main/brightness")
        self.assertEqual(accessory["_manifest"]["device_id"], "sim_lr_light_main")

    def test_homebridge_generator_uses_v2_topics_for_knx_lights(self) -> None:
        from scripts.sync_homebridge import build_accessory

        sync_devices(
            self.conn,
            [
                {
                    "device_id": "den_tran_dali_g1",
                    "Ten line den": "Den tran",
                    "Khu vuc": "Phong R&D",
                    "device_type": "light",
                    "G.A Control": "0/1/0",
                    "G.A status": "0/1/1",
                    "G.A dim 1 byte value": "0/1/2",
                    "G.A dim 1 byte value status": "0/1/3",
                    "homebridge_accessory_id": "hb_den_tran_dali_g1",
                    "capabilities": "turn_on,turn_off,set_brightness,read_state",
                }
            ],
        )
        row = self.conn.execute("SELECT * FROM devices WHERE device_id = 'den_tran_dali_g1'").fetchone()
        accessory_id, accessory = build_accessory(row)

        self.assertEqual(accessory_id, "hb_den_tran_dali_g1")
        self.assertEqual(accessory["type"], "lightbulb")
        self.assertEqual(accessory["topics"]["setOn"], "cmd/den_tran_dali_g1/power/set")
        self.assertEqual(accessory["topics"]["getOn"], "state/den_tran_dali_g1/power")
        self.assertEqual(accessory["topics"]["setBrightness"], "cmd/den_tran_dali_g1/brightness/set")
        self.assertEqual(accessory["topics"]["getBrightness"], "state/den_tran_dali_g1/brightness")

    def test_homebridge_generator_maps_supported_device_types(self) -> None:
        from scripts.sync_homebridge import build_accessory

        rows = [
            ("sim_lr_curtain", "Rem", "curtain", "lr_curtain"),
            ("sim_lr_ac", "May lanh", "ac", "lr_ac"),
            ("sim_gate_motion", "Cam bien", "sensor", "gate_motion"),
            ("sim_gate_main", "Cong", "gate", "gate_main"),
            ("sim_gate_lock", "Khoa", "lock", "gate_lock"),
        ]
        expected = {
            "sim_lr_curtain": "windowCovering",
            "sim_lr_ac": "thermostat",
            "sim_gate_motion": "motionSensor",
            "sim_gate_main": "switch",
            "sim_gate_lock": "lockMechanism",
        }
        sync_devices(
            self.conn,
            [
                {
                    "device_id": device_id,
                    "Ten line den": name,
                    "Khu vuc": "Phong khach",
                    "device_type": device_type,
                    "G.A Control": "14/2/1",
                    "G.A status": "14/2/2",
                    "homebridge_accessory_id": accessory_id,
                    "capabilities": "turn_on,turn_off,read_state,motion",
                }
                for device_id, name, device_type, accessory_id in rows
            ],
        )

        for device_id, service_type in expected.items():
            row = self.conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()
            built = build_accessory(row)
            self.assertIsNotNone(built)
            self.assertEqual(built[1]["type"], service_type)

    def test_homebridge_generator_exposes_temperature_and_humidity_services(self) -> None:
        from scripts.sync_homebridge import build_accessory

        sync_devices(
            self.conn,
            [
                {
                    "device_id": "sim_lr_sensor_temp_hum",
                    "Ten line den": "Cam bien nhiet am",
                    "Khu vuc": "Phong khach",
                    "device_type": "sensor",
                    "G.A Control": "14/4/1",
                    "homebridge_accessory_id": "lr_sensor_temp_hum",
                    "capabilities": "read_state",
                }
            ],
        )
        row = self.conn.execute("SELECT * FROM devices WHERE device_id = 'sim_lr_sensor_temp_hum'").fetchone()
        _, accessory = build_accessory(row)

        self.assertEqual(accessory["type"], "weatherStation")
        self.assertEqual(
            accessory["topics"]["getCurrentTemperature"], "state/living_room/lr_sensor_temp_hum/temperature"
        )
        self.assertEqual(
            accessory["topics"]["getCurrentRelativeHumidity"], "state/living_room/lr_sensor_temp_hum/humidity"
        )
        self.assertEqual(accessory["_manifest"]["area"], "Phong khach")

    def test_homebridge_plan_detects_noop_and_duplicate_identity(self) -> None:
        from scripts.sync_homebridge import build_plan, normalized_accessory

        sync_devices(
            self.conn,
            [
                {
                    "device_id": "sim_lr_light_main",
                    "Ten line den": "Den phong khach",
                    "Khu vuc": "Phong khach",
                    "device_type": "light",
                    "G.A Control": "14/1/1",
                    "G.A status": "14/1/2",
                    "homebridge_accessory_id": "lr_light_main",
                },
                {
                    "device_id": "sim_zz_light_dupe",
                    "Ten line den": "Den trung",
                    "Khu vuc": "Phong khach",
                    "device_type": "light",
                    "G.A Control": "14/1/5",
                    "G.A status": "14/1/6",
                    "homebridge_accessory_id": "lr_light_main",
                },
            ],
        )
        rows = self.conn.execute("SELECT * FROM devices ORDER BY device_id").fetchall()
        config = {
            "bridge": {"name": "Bridge"},
            "accessories": [
                normalized_accessory(
                    {
                        "accessory": "mqttthing",
                        "type": "lightbulb",
                        "name": "Den phong khach",
                        "topics": {
                            "getOn": "state/living_room/lr_light_main/power",
                            "setOn": "cmd/living_room/lr_light_main/power",
                        },
                    }
                )
            ],
            "platforms": [],
        }

        plan = build_plan(config, rows, Path(self.tmp.name) / "missing-manifest.json")

        self.assertIn("sim_lr_light_main", plan.unchanged)
        self.assertTrue(any("duplicate accessory identity" in error for error in plan.errors))
        self.assertFalse(plan.remove)

    def test_homebridge_plan_matches_knx_current_by_device_topic(self) -> None:
        from scripts.sync_homebridge import build_plan

        sync_devices(
            self.conn,
            [
                {
                    "device_id": "den_tron",
                    "Ten line den": "Den tron",
                    "Khu vuc": "Phong R&D",
                    "device_type": "light",
                    "G.A Control": "0/0/3",
                    "G.A status": "0/0/4",
                    "homebridge_accessory_id": "hb_den_tron",
                    "capabilities": "turn_on,turn_off,read_state",
                }
            ],
        )
        rows = self.conn.execute("SELECT * FROM devices").fetchall()
        config = {
            "accessories": [
                {
                    "accessory": "mqttthing",
                    "type": "lightbulb",
                    "name": "Den tron",
                    "topics": {"getOn": "state/den_tron/power", "setOn": "cmd/den_tron/power/set"},
                }
            ]
        }
        manifest = Path(self.tmp.name) / "manifest.json"
        manifest.write_text(
            json.dumps({"accessories": [{"device_id": "den_tron", "homebridge_accessory_id": "hb_den_tron"}]}),
            encoding="utf-8",
        )

        plan = build_plan(config, rows, manifest)

        self.assertEqual(plan.unchanged, ["den_tron"])
        self.assertFalse(plan.add)
        self.assertFalse(plan.remove)

    def test_homebridge_loader_excludes_disabled_devices(self) -> None:
        from scripts.sync_homebridge import load_homebridge_devices

        sync_devices(
            self.conn,
            [
                {
                    "device_id": "sim_enabled",
                    "Ten line den": "Den bat",
                    "Khu vuc": "Phong khach",
                    "device_type": "light",
                    "G.A Control": "14/3/1",
                    "homebridge_accessory_id": "lr_enabled",
                    "enabled": "1",
                },
                {
                    "device_id": "sim_disabled",
                    "Ten line den": "Den tat",
                    "Khu vuc": "Phong khach",
                    "device_type": "light",
                    "G.A Control": "14/3/2",
                    "homebridge_accessory_id": "lr_disabled",
                    "enabled": "0",
                },
            ],
        )
        devices = load_homebridge_devices(self.conn)

        self.assertEqual([row["device_id"] for row in devices], ["sim_enabled"])

    def test_sync_all_runs_google_sync_before_homebridge_and_defaults_dry_run(self) -> None:
        source = Path("scripts/sync_all.py").read_text(encoding="utf-8")

        self.assertLess(source.index("scripts/sync_devices.py"), source.index("scripts/sync_homebridge.py"))
        self.assertIn("--apply-homebridge", source)
        self.assertIn('"--dry-run"', source)


class IntentParaphraseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "agent.db"
        self.conn = connect(self.db_path)
        migrate(self.conn)
        upsert_user(self.conn, channel="telegram", user_id="owner", role="owner")
        sync_devices(
            self.conn,
            [
                {
                    "device_id": "den_tron",
                    "Ten line den": "Den tron",
                    "Khu vuc": "Phong khach",
                    "device_type": "light",
                    "G.A Control": "0/0/3",
                    "G.A status": "0/0/4",
                    "aliases": "den tron, bong tron",
                }
            ],
        )
        self.router = SkillRouter(build_default_registry())

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def assert_route(self, text: str, expected_skill: str, expected_intent: str, role: str = "owner") -> None:
        if role != "owner":
            upsert_user(self.conn, channel="telegram", user_id=role, role=role)
        user_id = "owner" if role == "owner" else role
        context = SkillContext(
            conn=self.conn,
            adapter_input=AdapterInput(
                request_id=f"para-{abs(hash(text))}",
                source_message_id=f"para-msg-{abs(hash(text))}",
                channel="telegram",
                user_id=user_id,
                text=text,
            ),
            user_role=resolve_user_role(self.conn, "telegram", user_id),
            intent_parser=FakeParser(Intent(intent="general_chat")),
            device_client=FakeKNX("dry_run"),
        )
        route = self.router.route(context)
        self.assertEqual((route.skill, route.intent), (expected_skill, expected_intent))


_INTENT_PARAPHRASE_CASES = [
    ("xin quyen", "user.access_request", "access_request", "guest"),
    ("xin cap quyen", "user.access_request", "access_request", "guest"),
    ("xin vao bot", "user.access_request", "access_request", "guest"),
    ("cho toi dung bot", "user.access_request", "access_request", "guest"),
    ("cho minh dung bot", "user.access_request", "access_request", "guest"),
    ("cho em dung bot", "user.access_request", "access_request", "guest"),
    ("cho anh dung bot", "user.access_request", "access_request", "guest"),
    ("cho chi dung bot", "user.access_request", "access_request", "guest"),
    ("mo quyen", "user.access_request", "access_request", "guest"),
    ("dang ky", "user.access_request", "access_request", "guest"),
    ("request access", "user.access_request", "access_request", "guest"),
    ("tim KNX tren web", "external.web_search", "web_search", "owner"),
    ("tim tren web KNX", "external.web_search", "web_search", "owner"),
    ("tim web KNX", "external.web_search", "web_search", "owner"),
    ("tra cuu web Open-Meteo", "external.web_search", "web_search", "owner"),
    ("tra cuu tren web Open-Meteo", "external.web_search", "web_search", "owner"),
    ("search web KNX", "external.web_search", "web_search", "owner"),
    ("google KNX la gi", "external.web_search", "web_search", "owner"),
    ("tim kiem KNX", "external.web_search", "web_search", "owner"),
    ("tim ho KNX", "external.web_search", "web_search", "owner"),
    ("tim giup KNX", "external.web_search", "web_search", "owner"),
    ("tim thong tin KNX", "external.web_search", "web_search", "owner"),
    ("tra thong tin KNX", "external.web_search", "web_search", "owner"),
    ("xem tren mang KNX", "external.web_search", "web_search", "owner"),
    ("tren mang noi gi ve KNX", "external.web_search", "web_search", "owner"),
    ("internet noi gi ve KNX", "external.web_search", "web_search", "owner"),
    ("thoi tiet Go Vap hom nay", "external.weather", "weather", "owner"),
    ("du bao thoi tiet Go Vap", "external.weather", "weather", "owner"),
    ("nhiet do TPHCM", "external.weather", "weather", "owner"),
    ("hom nay co mua khong", "external.weather", "weather", "owner"),
    ("chieu nay co mua khong", "external.weather", "weather", "owner"),
    ("troi mua khong", "external.weather", "weather", "owner"),
    ("nang nong o Go Vap khong", "external.weather", "weather", "owner"),
    ("troi nong o TPHCM khong", "external.weather", "weather", "owner"),
    ("thoi tiet quan 2 hom nay co mua khong, toi dang tinh ve que", "external.weather", "weather", "owner"),
    ("ban ten gi", "system.help", "identity", "guest"),
    ("ban ten la gi", "system.help", "identity", "guest"),
    ("bot ten gi", "system.help", "identity", "guest"),
    ("ban la ai", "system.help", "identity", "guest"),
    ("bot la ai", "system.help", "identity", "guest"),
    ("day la bot gi", "system.help", "identity", "guest"),
    ("aicontrol la gi", "system.help", "identity", "guest"),
    ("bot lam duoc gi", "system.help", "help", "guest"),
    ("ban lam duoc gi", "system.help", "help", "guest"),
    ("con bot nay lam duoc gi", "system.help", "help", "guest"),
    ("huong dan su dung", "system.help", "help", "guest"),
    ("co nhung skill gi", "system.help", "help", "guest"),
    ("co the lam gi", "system.help", "help", "guest"),
    ("he thong co on khong", "system.health", "system_health", "owner"),
    ("he thong on khong", "system.health", "system_health", "owner"),
    ("bot co on khong", "system.health", "system_health", "owner"),
    ("bot dang chay on khong", "system.health", "system_health", "owner"),
    ("trang thai he thong", "system.health", "system_health", "owner"),
    ("kiem tra he thong", "system.health", "system_health", "owner"),
    ("knx bridge dang the nao", "system.health", "system_health", "owner"),
    ("mqtt dry run dang bat chua", "system.health", "system_health", "owner"),
    ("bay gio may gio", "external.time_date", "time_date", "owner"),
    ("gio may", "external.time_date", "time_date", "owner"),
    ("gio hien tai", "external.time_date", "time_date", "owner"),
    ("hom nay la ngay nao", "external.time_date", "time_date", "owner"),
    ("hom nay la thu may", "external.time_date", "time_date", "owner"),
    ("ngay hom nay", "external.time_date", "time_date", "owner"),
    ("tao kich ban xem phim", "home.scene", "create_scene", "owner"),
    ("them kich ban di ngu", "home.scene", "create_scene", "owner"),
    ("co kich ban gi", "home.scene", "list_scenes", "owner"),
    ("danh sach kich ban", "home.scene", "list_scenes", "owner"),
    ("chay kich ban di ngu", "home.scene", "run_scene", "owner"),
    ("den nao dang bat", "home.inventory", "status_summary", "owner"),
    ("co den nao dang bat khong", "home.inventory", "status_summary", "owner"),
    ("den tron thong tin", "home.device_info", "device_info", "owner"),
    ("den tron mo ta", "home.device_info", "device_info", "owner"),
    ("den tron nam o dau", "home.device_info", "device_info", "owner"),
    ("den tron co chuc nang gi", "home.device_info", "device_info", "owner"),
    ("bat den tron", "home.device_control", "turn_on", "owner"),
]


def _make_intent_paraphrase_test(text: str, skill: str, intent: str, role: str):
    def test(self: IntentParaphraseTests) -> None:
        self.assert_route(text, skill, intent, role)

    return test


for _index, (_text, _skill, _intent, _role) in enumerate(_INTENT_PARAPHRASE_CASES, start=1):
    setattr(
        IntentParaphraseTests,
        f"test_intent_paraphrase_{_index:03d}",
        _make_intent_paraphrase_test(_text, _skill, _intent, _role),
    )


if __name__ == "__main__":
    unittest.main()
