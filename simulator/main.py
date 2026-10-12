import json
import time
import os
import threading
import random
import sys
from pathlib import Path
import pg8000.native
import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smart_home_agent.integrations.mqtt_contract import (
    build_state_payload,
    parse_command_topic,
    state_topic,
    validate_command_payload,
)

# --- Cấu hình ---
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_USER = os.environ.get("DB_USER", "smarthome")
DB_PASS = os.environ.get("DB_PASS", "smarthome_password")
DB_NAME = os.environ.get("DB_NAME", "smarthome_db")
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))

# Nạp danh sách thiết bị
try:
    with open("devices.json", "r", encoding="utf-8") as f:
        devices = json.load(f)
except Exception as e:
    print(f"Error loading devices.json: {e}")
    devices = []

device_dict = {d["id"]: d for d in devices}

# --- Database Seeding ---
def seed_database():
    try:
        conn = pg8000.native.Connection(user=DB_USER, password=DB_PASS, host=DB_HOST, database=DB_NAME)

        for dev in devices:
            # Upsert into devices
            conn.run("""
                INSERT INTO devices (id, room, type, group_address, capabilities, is_simulated)
                VALUES (:id, :room, :type, :group_address, CAST(:capabilities AS JSONB), :is_simulated)
                ON CONFLICT (id) DO UPDATE SET
                    room = EXCLUDED.room,
                    type = EXCLUDED.type,
                    group_address = EXCLUDED.group_address,
                    capabilities = EXCLUDED.capabilities,
                    is_simulated = EXCLUDED.is_simulated
            """,
                id=dev["id"], room=dev["room"], type=dev["type"],
                group_address=dev["group_address"],
                capabilities=json.dumps(dev["capabilities"]),
                is_simulated=True
            )

            # Upsert into device_states
            conn.run("""
                INSERT INTO device_states (device_id, state)
                VALUES (:device_id, CAST(:state AS JSONB))
                ON CONFLICT (device_id) DO NOTHING
            """, device_id=dev["id"], state=json.dumps(dev["state"]))

        conn.close()
        print("Database seeded successfully.")
    except Exception as e:
        print(f"Database seed failed: {e}")

# --- DB Update ---
def update_db_state(device_id, state):
    try:
        conn = pg8000.native.Connection(user=DB_USER, password=DB_PASS, host=DB_HOST, database=DB_NAME)
        conn.run("""
            UPDATE device_states SET state = CAST(:state AS JSONB), updated_at = CURRENT_TIMESTAMP
            WHERE device_id = :device_id
        """, state=json.dumps(state), device_id=device_id)
        conn.close()
    except Exception as e:
        print(f"Failed to update state in DB for {device_id}: {e}")

# --- MQTT Callbacks ---
def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT broker with result code {rc}")
    client.subscribe("cmd/+/+/set")

def on_message(client, userdata, msg):
    try:
        parsed = parse_command_topic(msg.topic)
        if parsed.action == "set":
            device_id = parsed.device_id

            if device_id in device_dict:
                payload = json.loads(msg.payload.decode())
                validate_command_payload(payload)
                print(f"Received command for {device_id}: {payload}")

                # Giả lập delay của thiết bị thật (0.1 - 0.5s)
                time.sleep(random.uniform(0.1, 0.5))

                # Cập nhật state nội bộ
                device = device_dict[device_id]
                device["state"][parsed.capability] = payload["value"]

                # Publish state mới
                topic = state_topic(device_id, parsed.capability)
                state_payload = build_state_payload(device_id, parsed.capability, payload["value"], source="simulator")
                client.publish(topic, json.dumps(state_payload), retain=True)

                # Ghi vào DB
                update_db_state(device_id, device["state"])
                print(f"Updated state for {device_id}")
    except Exception as e:
        print(f"Error handling message: {e}")

# --- Telemetry Simulation ---
def telemetry_loop(client):
    while True:
        try:
            for dev in devices:
                if dev["type"] == "sensor":
                    continue
                    # Random noise cho cảm biến
                    if "temperature" in dev["state"]:
                        dev["state"]["temperature"] += random.uniform(-0.5, 0.5)
                        dev["state"]["temperature"] = round(dev["state"]["temperature"], 1)
                    if "humidity" in dev["state"]:
                        dev["state"]["humidity"] += random.uniform(-1, 1)
                        dev["state"]["humidity"] = round(max(0, min(100, dev["state"]["humidity"])), 1)
                    if "motion" in dev["state"]:
                        # 5% cơ hội phát hiện chuyển động
                        dev["state"]["motion"] = random.random() < 0.05

                    topic = state_topic(dev["id"], "power")
                    client.publish(topic, json.dumps(dev["state"]))

        except Exception as e:
            print(f"Telemetry error: {e}")

        # Gửi telemetry mỗi 10 giây
        time.sleep(10)

if __name__ == "__main__":
    print("Starting Room Simulator...")
    seed_database()

    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            break
        except ConnectionRefusedError:
            print("MQTT broker not ready, retrying in 5 seconds...")
            time.sleep(5)

    # Bắt đầu luồng telemetry
    threading.Thread(target=telemetry_loop, args=(client,), daemon=True).start()

    # Lắng nghe message
    client.loop_forever()
