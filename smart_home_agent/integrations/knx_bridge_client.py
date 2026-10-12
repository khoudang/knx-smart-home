from __future__ import annotations

import json
import os
import re
import time
from dataclasses import replace
from typing import Any

from smart_home_agent.integrations.device_control import ControlCommand, ControlResult
from smart_home_agent.integrations.mqtt_contract import (
    build_command_payload,
    build_result_payload,
    capability_for_intent,
    command_topic,
    result_topic,
)


GA_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{1,3}$")
SUCCESS_STATUSES = {"success", "accepted_but_unverified", "dry_run"}


class KNXBridgeClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        dry_run: bool = True,
        allowed_device_ids: list[str] | None = None,
        allowed_group_addresses: list[str] | None = None,
        blocked_device_types: list[str] | None = None,
        blocked_group_addresses: list[str] | None = None,
        timeout_seconds: float = 3.0,
        retries: int = 1,
        mqtt_host: str | None = None,
        mqtt_port: int = 1883,
        mqtt_username: str | None = None,
        mqtt_password: str | None = None,
        mqtt_dry_run: bool = True,
        publisher: Any = None,
        state_reader: Any = None,
        result_publisher: Any = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token = token
        self.dry_run = dry_run
        self.mqtt_dry_run = mqtt_dry_run
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.mqtt_username = mqtt_username
        self.mqtt_password = mqtt_password
        self.publisher = publisher
        self.state_reader = state_reader
        self.result_publisher = result_publisher
        self.allowed_device_ids = set(allowed_device_ids or [])
        self.allowed_group_addresses = set(allowed_group_addresses or [])
        self.blocked_device_types = set(blocked_device_types or ["gate", "lock", "alarm"])
        self.blocked_group_addresses = set(blocked_group_addresses or [])
        self.timeout_seconds = timeout_seconds
        self.retries = max(0, retries)
        self.sent_commands: list[ControlCommand] = []

    @classmethod
    def from_env(cls) -> "KNXBridgeClient":
        return cls(
            base_url=os.environ.get("KNX_BRIDGE_URL"),
            token=os.environ.get("KNX_BRIDGE_TOKEN"),
            dry_run=os.environ.get("KNX_DRY_RUN", "true").lower() in {"1", "true", "yes", "on"},
            allowed_device_ids=_csv(os.environ.get("KNX_ALLOWED_TEST_DEVICE_IDS", "")),
            allowed_group_addresses=_csv(os.environ.get("KNX_ALLOWED_TEST_GROUP_ADDRESSES", "")),
            blocked_device_types=_csv(os.environ.get("KNX_BLOCKED_DEVICE_TYPES", "gate,lock,alarm")),
            blocked_group_addresses=_csv(os.environ.get("KNX_BLOCKED_GROUP_ADDRESSES", "")),
            mqtt_host=os.environ.get("MQTT_HOST", "localhost"),
            mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
            mqtt_username=os.environ.get("MQTT_USERNAME"),
            mqtt_password=os.environ.get("MQTT_PASSWORD"),
            mqtt_dry_run=os.environ.get("MQTT_DRY_RUN", os.environ.get("KNX_DRY_RUN", "true")).lower()
            in {"1", "true", "yes", "on"},
        )

    def health_check(self) -> ControlResult:
        if self.dry_run:
            return ControlResult("dry_run", "KNX dry-run is enabled")
        if self.mqtt_dry_run:
            return ControlResult("dry_run", "MQTT dry-run is enabled")
        if not self.mqtt_host:
            return ControlResult("bridge_unavailable", "MQTT host is not configured")
        return ControlResult("success", "MQTT command publisher is configured")

    def execute(self, command: ControlCommand) -> ControlResult:
        command = _normalise_value(command)
        blocked = self._staging_block(command)
        if blocked:
            return blocked

        target_ga = _target_ga(command)
        if not target_ga:
            return ControlResult("unsupported_capability", f"{command.device_id} does not support {command.action}")
        invalid = self._invalid_ga([target_ga, _status_ga(command)])
        if invalid:
            return invalid

        self.sent_commands.append(command)
        capability = capability_for_intent(command.action)
        request_id = _request_id(command)
        topic = command_topic(command.device_id, capability)
        payload = build_command_payload(request_id, command.device_id, capability, _mqtt_value(command))
        payload = {
            **payload,
        }
        if self.dry_run and self.mqtt_dry_run:
            result_payload = build_result_payload(
                request_id,
                "dry_run",
                resolved_device_id=command.device_id,
                resolved_capability=capability,
            )
            self._publish_result(request_id, result_payload)
            return ControlResult("dry_run", "Dry-run: MQTT command was not published", result_payload)

        published = self._publish(topic, payload, retain=False)
        if published.status != "success":
            return published
        self._publish_result(request_id, build_result_payload(request_id, "accepted"))

        if self.dry_run:
            return ControlResult(
                "dry_run",
                "MQTT command was published; KNX bridge dry-run is expected",
                {"topic": topic, "payload": payload},
            )

        if not _status_ga(command):
            return ControlResult("accepted_but_unverified", "Command accepted but no status group address is configured")
        state = self.read_state(command)
        if state.status != "success":
            return state
        if command.expected_state and not _state_matches(state.state, command.expected_state):
            return ControlResult("state_mismatch", "KNX state did not match expected value", state.state)
        return state

    def read_state(self, command: ControlCommand) -> ControlResult:
        status_ga = _status_ga(command)
        if not status_ga:
            return ControlResult("accepted_but_unverified", "No KNX status group address is configured")
        invalid = self._invalid_ga([status_ga])
        if invalid:
            return invalid
        if self.dry_run:
            return ControlResult("dry_run", "Dry-run: KNX state was not read", _dry_state(command))
        if not self.state_reader:
            return ControlResult("accepted_but_unverified", "No MQTT state reader is configured")
        capability = capability_for_intent(command.action) if command.action != "read_state" else _read_capability(command)
        try:
            state = self.state_reader(command.device_id, capability, self.timeout_seconds)
        except TimeoutError:
            return ControlResult("knx_timeout", "Timed out waiting for KNX state")
        result = ControlResult("success", state=state)
        if result.status == "success" and command.expected_state and not _state_matches(result.state, command.expected_state):
            return ControlResult("state_mismatch", "KNX state did not match expected value", result.state)
        return result

    def _staging_block(self, command: ControlCommand) -> ControlResult | None:
        if command.device_id.startswith("sim_"):
            return ControlResult("unsupported_capability", "Simulated devices are never sent to KNX")
        if command.device_type in self.blocked_device_types:
            return ControlResult("unsupported_capability", "Device type is blocked for KNX staging")
        addresses = [ga for ga in _all_gas(command) if ga]
        if any(ga in self.blocked_group_addresses for ga in addresses):
            return ControlResult("unsupported_capability", "Group address is blocked for KNX staging")
        if self.dry_run and self.allowed_device_ids and command.device_id not in self.allowed_device_ids:
            return ControlResult("unsupported_capability", "Device is not in KNX_ALLOWED_TEST_DEVICE_IDS")
        if self.dry_run and self.allowed_group_addresses and not any(ga in self.allowed_group_addresses for ga in addresses):
            return ControlResult("unsupported_capability", "Group address is not in KNX_ALLOWED_TEST_GROUP_ADDRESSES")
        return None

    def _invalid_ga(self, addresses: list[str | None]) -> ControlResult | None:
        for address in addresses:
            if address and not GA_RE.match(address):
                return ControlResult("invalid_group_address", f"Invalid KNX group address for configured device")
        return None

    def _publish(self, topic: str, payload: dict[str, Any], retain: bool) -> ControlResult:
        if self.publisher:
            self.publisher(topic, json.dumps(payload), retain)
            return ControlResult("success", state={"topic": topic, "payload": payload})
        if not self.mqtt_host:
            return ControlResult("bridge_unavailable", "MQTT host is not configured")
        last_error = "MQTT publish failed"
        for attempt in range(self.retries + 1):
            try:
                import paho.mqtt.publish as mqtt_publish

                auth = None
                if self.mqtt_username:
                    auth = {"username": self.mqtt_username, "password": self.mqtt_password}
                mqtt_publish.single(
                    topic,
                    payload=json.dumps(payload),
                    hostname=self.mqtt_host,
                    port=self.mqtt_port,
                    auth=auth,
                    retain=retain,
                )
                return ControlResult("success", state={"topic": topic, "payload": payload})
            except TimeoutError:
                return ControlResult("knx_timeout", "MQTT publish timed out")
            except Exception as exc:
                last_error = str(exc)
            if attempt < self.retries:
                time.sleep(0.1)
        return ControlResult("bridge_unavailable", last_error)

    def _publish_result(self, request_id: str, payload: dict[str, Any]) -> None:
        if self.result_publisher:
            self.result_publisher(result_topic(request_id), json.dumps(payload), False)


def _target_ga(command: ControlCommand) -> str | None:
    if command.action in {"turn_on", "turn_off"}:
        return command.control_ga
    if command.action == "set_brightness":
        return command.dim_control_ga or command.control_ga
    if command.action == "set_color":
        return command.color_control_ga
    return command.control_ga


def _request_id(command: ControlCommand) -> str:
    if command.request_id:
        return command.request_id
    return f"{command.device_id}-{int(time.time() * 1000)}"


def _mqtt_value(command: ControlCommand) -> Any:
    if command.action == "turn_on":
        return True
    if command.action == "turn_off":
        return False
    if command.action == "set_brightness":
        return int(command.value)
    if command.action == "set_color":
        return command.value
    if command.action == "run_scene":
        return "activate"
    return command.value


def _read_capability(command: ControlCommand) -> str:
    if command.color_status_ga:
        return "color"
    if command.dim_status_ga:
        return "brightness"
    return "power"


def _status_ga(command: ControlCommand) -> str | None:
    if command.action == "set_brightness":
        return command.dim_status_ga or command.status_ga
    if command.action == "set_color":
        return command.color_status_ga
    return command.status_ga


def _all_gas(command: ControlCommand) -> list[str | None]:
    return [
        command.control_ga,
        command.status_ga,
        command.dim_control_ga,
        command.dim_status_ga,
        command.color_control_ga,
        command.color_status_ga,
    ]


def _normalise_value(command: ControlCommand) -> ControlCommand:
    if command.action == "turn_on":
        return replace(command, value=True, expected_state=command.expected_state or {"state": "on"})
    if command.action == "turn_off":
        return replace(command, value=False, expected_state=command.expected_state or {"state": "off"})
    if command.action == "set_brightness":
        return replace(command, value=command.value if command.value is not None else command.expected_state)
    return command


def _dry_state(command: ControlCommand) -> dict[str, Any]:
    state = {"device_id": command.device_id, "action": command.action, "value": command.value}
    if command.expected_state:
        state.update(command.expected_state)
    return state


def _state_matches(state: Any, expected: dict[str, Any]) -> bool:
    if not isinstance(state, dict):
        return False
    return all(state.get(key) == value for key, value in expected.items())


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
