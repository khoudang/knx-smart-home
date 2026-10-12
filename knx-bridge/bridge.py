from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
from xknx import XKNX
from xknx.io import ConnectionConfig, ConnectionType
from xknx.telegram import GroupAddress, Telegram
from xknx.telegram.apci import GroupValueWrite

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smart_home_agent.integrations.mqtt_contract import (
    build_result_payload,
    build_state_payload,
    parse_command_topic,
    result_topic,
    state_topic,
    validate_command_payload,
)


MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD")
MQTT_DRY_RUN = os.environ.get("MQTT_DRY_RUN", os.environ.get("KNX_DRY_RUN", "true")).lower() in {
    "1",
    "true",
    "yes",
    "on",
}
KNX_DRY_RUN = os.environ.get("KNX_DRY_RUN", "true").lower() in {"1", "true", "yes", "on"}
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", "data/smart_home.db"))
KNX_IP = os.environ.get("KNX_IP")
KNX_PORT = int(os.environ.get("KNX_PORT", "3671"))
IDEMPOTENCY_TTL_SECONDS = int(os.environ.get("MQTT_IDEMPOTENCY_TTL_SECONDS", "600"))


devices_by_id: dict[str, dict[str, Any]] = {}
ga_to_state: dict[str, tuple[str, str]] = {}
processed_requests: dict[str, tuple[float, dict[str, Any]]] = {}
xknx_instance: XKNX | None = None
loop: asyncio.AbstractEventLoop | None = None


def load_devices_from_sqlite() -> None:
    if not DATABASE_PATH.exists():
        raise SystemExit(f"SQLite database not found: {DATABASE_PATH}")
    conn = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT device_id, device_type, capabilities, control_ga, status_ga,
                   dim_control_ga, dim_status_ga, color_control_ga, color_status_ga
            FROM devices
            WHERE enabled = 1 AND sync_status != 'deleted_from_sheet'
            """
        ).fetchall()
    finally:
        conn.close()

    devices_by_id.clear()
    ga_to_state.clear()
    for row in rows:
        device = dict(row)
        device["capabilities"] = json.loads(device.get("capabilities") or "[]")
        devices_by_id[device["device_id"]] = device
        for capability, field in (
            ("power", "status_ga"),
            ("brightness", "dim_status_ga"),
            ("color", "color_status_ga"),
        ):
            if device.get(field):
                ga_to_state[device[field]] = (device["device_id"], capability)
    print(json.dumps({"event": "devices_loaded", "count": len(devices_by_id)}))


async def connect_xknx() -> XKNX:
    if KNX_DRY_RUN:
        raise RuntimeError("connect_xknx must not run in dry-run")
    if not KNX_IP:
        raise ValueError("KNX_IP is not configured")
    xknx = XKNX(
        connection_config=ConnectionConfig(
            connection_type=ConnectionType.TUNNELING,
            gateway_ip=KNX_IP,
            gateway_port=KNX_PORT,
        )
    )

    def on_telegram(telegram: Telegram) -> None:
        if not isinstance(telegram.payload, GroupValueWrite):
            return
        ga = str(telegram.destination_address)
        mapping = ga_to_state.get(ga)
        if not mapping:
            return
        device_id, capability = mapping
        raw_value = telegram.payload.value.value
        value = _normalise_state_value(capability, raw_value)
        payload = build_state_payload(device_id, capability, value)
        mqtt_client.publish(state_topic(device_id, capability), json.dumps(payload), retain=True)

    xknx.telegram_queue.register_telegram_received_cb(on_telegram)
    await xknx.start()
    print(json.dumps({"event": "knx_connected"}))
    return xknx


def on_mqtt_connect(client, userdata, flags, rc):
    print(json.dumps({"event": "mqtt_connected", "rc": rc}))
    client.subscribe("cmd/+/+/set", qos=1)
    client.subscribe("cmd/+/scene/activate", qos=1)


def on_mqtt_message(client, userdata, msg):
    try:
        result = handle_command_message(msg.topic, msg.payload.decode("utf-8"))
    except Exception as exc:
        result = build_result_payload(_request_id_from_payload(msg.payload.decode("utf-8")), "validation_failed", error_code=str(exc))
    client.publish(result_topic(result["request_id"]), json.dumps(result), qos=1, retain=False)


def handle_command_message(topic: str, payload_text: str) -> dict[str, Any]:
    parsed = parse_command_topic(topic)
    payload = _command_payload(parsed, payload_text)
    validate_command_payload(payload)
    request_id = str(payload["request_id"])

    duplicate = _duplicate_result(request_id)
    if duplicate:
        return duplicate

    if payload["device_id"] != parsed.device_id or payload["capability"] != parsed.capability:
        return _remember(request_id, build_result_payload(request_id, "validation_failed", "topic_payload_mismatch"))

    device = devices_by_id.get(parsed.device_id)
    if not device:
        return _remember(request_id, build_result_payload(request_id, "unknown_device", "unknown_device"))
    if not _capability_supported(device, parsed.capability):
        return _remember(request_id, build_result_payload(request_id, "unsupported_capability", "unsupported_capability"))

    control_ga = _control_ga(device, parsed.capability)
    if not control_ga:
        return _remember(request_id, build_result_payload(request_id, "missing_control_address", "missing_control_address"))

    if KNX_DRY_RUN or MQTT_DRY_RUN:
        return _remember(
            request_id,
            build_result_payload(
                request_id,
                "dry_run",
                resolved_device_id=parsed.device_id,
                resolved_capability=parsed.capability,
            ),
        )

    _send_knx(control_ga, parsed.capability, payload["value"])
    return _remember(request_id, build_result_payload(request_id, "accepted"))


def _command_payload(parsed, payload_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        payload = payload_text.strip()
    if isinstance(payload, dict):
        return payload
    return {
        "schema_version": 1,
        "request_id": f"homebridge-{parsed.device_id}-{parsed.capability}-{int(time.time() * 1000)}",
        "device_id": parsed.device_id,
        "capability": parsed.capability,
        "value": _normalise_homebridge_value(parsed.capability, payload),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _normalise_homebridge_value(capability: str, value: Any) -> Any:
    if capability == "power":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "on", "yes"}
    if capability == "brightness":
        return int(float(value))
    if capability == "color":
        return value
    return value


def _request_id_from_payload(payload_text: str) -> str:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return "unknown"
    request_id = payload.get("request_id")
    return str(request_id) if request_id else "unknown"


def _send_knx(group_address: str, capability: str, value: Any) -> None:
    if not xknx_instance or not loop:
        raise RuntimeError("KNX is not connected")
    encoded = _encode_knx_value(capability, value)
    telegram = Telegram(destination_address=GroupAddress(group_address), payload=GroupValueWrite(encoded))
    asyncio.run_coroutine_threadsafe(xknx_instance.telegrams.put(telegram), loop)


def _control_ga(device: dict[str, Any], capability: str) -> str | None:
    if capability == "power":
        return device.get("control_ga")
    if capability == "brightness":
        return device.get("dim_control_ga")
    if capability == "color":
        return device.get("color_control_ga")
    if capability == "scene":
        return device.get("control_ga")
    return None


def _capability_supported(device: dict[str, Any], capability: str) -> bool:
    capabilities = set(device.get("capabilities") or [])
    if capability == "power":
        return "turn_on" in capabilities or "turn_off" in capabilities
    if capability == "brightness":
        return "set_brightness" in capabilities
    if capability == "color":
        return "set_color" in capabilities
    if capability == "scene":
        return device.get("device_type") == "scene" or "run_scene" in capabilities
    return False


def _encode_knx_value(capability: str, value: Any) -> Any:
    if capability == "power":
        return 1 if value else 0
    if capability == "brightness":
        return int(value)
    if capability == "color":
        return value
    if capability == "scene":
        return 1
    raise ValueError("unsupported capability")


def _normalise_state_value(capability: str, raw_value: Any) -> Any:
    if capability == "power":
        return bool(raw_value)
    if capability == "brightness":
        return int(raw_value)
    return raw_value


def _duplicate_result(request_id: str) -> dict[str, Any] | None:
    now = time.time()
    expired = [key for key, (ts, _) in processed_requests.items() if now - ts > IDEMPOTENCY_TTL_SECONDS]
    for key in expired:
        processed_requests.pop(key, None)
    existing = processed_requests.get(request_id)
    return existing[1] if existing else None


def _remember(request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    processed_requests[request_id] = (time.time(), payload)
    return payload


mqtt_client = mqtt.Client(client_id=os.environ.get("MQTT_BRIDGE_CLIENT_ID", "knx-bridge"))
mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_message = on_mqtt_message
if MQTT_USERNAME:
    mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)


async def main() -> None:
    global xknx_instance, loop
    loop = asyncio.get_running_loop()
    load_devices_from_sqlite()
    if "--dry-run" in sys.argv:
        print(json.dumps({"event": "dry_run_ok", "knx_dry_run": KNX_DRY_RUN, "mqtt_dry_run": MQTT_DRY_RUN}))
        return
    if not KNX_DRY_RUN:
        xknx_instance = await connect_xknx()
    mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
    mqtt_client.loop_start()
    try:
        await asyncio.Event().wait()
    finally:
        mqtt_client.loop_stop()
        if xknx_instance:
            await xknx_instance.stop()


if __name__ == "__main__":
    asyncio.run(main())
