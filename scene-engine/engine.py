import os
import json
import time
import threading
import sys
from pathlib import Path
import pg8000.native
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smart_home_agent.integrations.mqtt_contract import build_command_payload, command_topic

load_dotenv()

# --- Config ---
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_USER = os.environ.get("DB_USER", "smarthome")
DB_PASS = os.environ.get("DB_PASS", "smarthome_password")
DB_NAME = os.environ.get("DB_NAME", "smarthome_db")
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))

# --- Global State ---
active_scenes = set()
active_scenes_lock = threading.Lock()
shutdown_event = threading.Event()

# --- DB Connection Helper ---
def get_db_connection():
    return pg8000.native.Connection(user=DB_USER, password=DB_PASS, host=DB_HOST, database=DB_NAME)

# --- History Logger ---
def log_history(conn, device_id, action_name, params, result, scene_id=None):
    try:
        actor = f"scene:{scene_id}" if scene_id else "scene"
        conn.run("""
            INSERT INTO device_history (device_id, action, params, result, actor)
            VALUES (:device_id, :action, CAST(:params AS JSONB), :result, :actor)
        """, device_id=device_id, action=action_name, params=json.dumps(params), result=result, actor=actor)
    except Exception as e:
        print(f"[{scene_id}] Error logging history for {device_id}: {e}")

# --- Helper: Publish with Retry ---
def publish_with_retry(client, topic, payload, scene_id, max_retries=2):
    for attempt in range(max_retries + 1):
        msg_info = client.publish(topic, payload)
        # wait_for_publish blocks until the message is sent
        msg_info.wait_for_publish(timeout=2.0)
        if msg_info.is_published():
            return True
        print(f"[{scene_id}] Publish to {topic} failed. Retry {attempt + 1}/{max_retries}...")
        time.sleep(0.5)
    return False

# --- Execute Action ---
def execute_action(conn, client, scene_id, action):
    # action contains: action_type, params, target_device_id, room
    device_id = action['target_device_id']
    action_type = action['action_type']
    params = action['params']
    capability, value = _legacy_action_to_contract(action_type, params)
    topic = command_topic(device_id, capability)

    if action_type == 'set':
        payload = json.dumps(build_command_payload(f"{scene_id}:{device_id}:set", device_id, capability, value))
        print(f"[{scene_id}] Executing SET -> {topic}: {payload}")
        if publish_with_retry(client, topic, payload, scene_id):
            log_history(conn, device_id, 'set', params, 'success', scene_id)
        else:
            log_history(conn, device_id, 'set', params, 'failed', scene_id)
            print(f"[{scene_id}] CRITICAL: Failed to publish SET to {device_id}")

    elif action_type == 'pulse':
        on_payload = build_command_payload(f"{scene_id}:{device_id}:pulse:on", device_id, "power", True)
        off_payload = build_command_payload(f"{scene_id}:{device_id}:pulse:off", device_id, "power", False)
        duration_ms = params.get('on_duration_ms', 1000)

        print(f"[{scene_id}] Executing PULSE -> {topic} for {duration_ms}ms")

        # Turn ON
        if publish_with_retry(client, topic, json.dumps(on_payload), scene_id):
            log_history(conn, device_id, 'pulse_on', on_payload, 'success', scene_id)
        else:
            log_history(conn, device_id, 'pulse_on', on_payload, 'failed', scene_id)
            print(f"[{scene_id}] CRITICAL: Failed to publish PULSE_ON to {device_id}. Aborting pulse.")
            return # Don't sleep and turn off if turn on failed!

        # Sleep with interruption check
        is_interrupted = shutdown_event.wait(duration_ms / 1000.0)
        if is_interrupted:
            print(f"[{scene_id}] WARNING: Engine stopped during PULSE sleep. Attempting emergency OFF for {device_id}...")

        # Turn OFF
        if publish_with_retry(client, topic, json.dumps(off_payload), scene_id):
            log_history(conn, device_id, 'pulse_off', off_payload, 'success', scene_id)
        else:
            log_history(conn, device_id, 'pulse_off', off_payload, 'failed', scene_id)
            print(f"[{scene_id}] CRITICAL: Failed to publish PULSE_OFF to {device_id}. DEVICE MAY BE STUCK ON!")

    else:
        print(f"[{scene_id}] Unknown action type: {action_type}")

# --- Scene Execution Thread ---
def run_scene(scene_id, client):
    with active_scenes_lock:
        if scene_id in active_scenes:
            print(f"[{scene_id}] Scene is already running. Ignoring duplicate trigger.")
            return
        active_scenes.add(scene_id)

    print(f"[{scene_id}] Triggered.")

    # 2. Connection DB theo thread
    conn = None
    try:
        conn = get_db_connection()

        # Verify scene is approved
        scenes = conn.run("SELECT id, name, status FROM scenes WHERE id = :scene_id", scene_id=scene_id)
        if not scenes:
            print(f"[{scene_id}] Scene not found.")
            return

        scene = scenes[0]
        if scene[2] != 'approved':
            print(f"[{scene_id}] Scene is not approved (status: {scene[2]}). Execution aborted.")
            return

        print(f"[{scene_id}] Validated scene '{scene[1]}'. Loading actions...")

        # Fetch actions
        actions = conn.run("""
            SELECT sa.action_type, sa.params, sa.device_id, d.room
            FROM scene_actions sa
            JOIN devices d ON sa.device_id = d.id
            WHERE sa.scene_id = :scene_id
            ORDER BY sa.action_order ASC
        """, scene_id=scene_id)

        if not actions:
            print(f"[{scene_id}] No actions found.")
            return

        for act in actions:
            if shutdown_event.is_set():
                print(f"[{scene_id}] WARNING: Execution aborted due to engine shutdown.")
                break

            action_dict = {
                "action_type": act[0],
                "params": act[1],
                "target_device_id": act[2],
                "room": act[3]
            }
            execute_action(conn, client, scene_id, action_dict)

        print(f"[{scene_id}] Execution completed successfully.")

    except Exception as e:
        print(f"[{scene_id}] Engine error: {e}")
    finally:
        if conn:
            conn.close()
        with active_scenes_lock:
            active_scenes.discard(scene_id)


def _legacy_action_to_contract(action_type, params):
    if action_type == "pulse":
        return "power", True
    if "brightness" in params:
        return "brightness", int(params["brightness"])
    if "color" in params:
        return "color", params["color"]
    state = str(params.get("state", "")).upper()
    if state in {"ON", "TRUE", "1"}:
        return "power", True
    if state in {"OFF", "FALSE", "0"}:
        return "power", False
    return "power", bool(params)

# --- MQTT Callbacks ---
def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT broker with code {rc}")
    client.subscribe("cmd/+/scene/activate")
    print("Subscribed to topic: cmd/+/scene/activate")

def on_message(client, userdata, msg):
    try:
        topic_parts = msg.topic.split('/')
        if len(topic_parts) == 4 and topic_parts[0] == "cmd" and topic_parts[2] == "scene" and topic_parts[3] == "activate":
            scene_id = topic_parts[1]
            threading.Thread(target=run_scene, args=(scene_id, client), daemon=True).start()
    except Exception as e:
        print(f"Error processing message {msg.topic}: {e}")

if __name__ == "__main__":
    print("Starting Scene Engine v2...")
    mqtt_client = mqtt.Client(client_id="scene-engine")
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message

    try:
        mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
        mqtt_client.loop_forever()
    except KeyboardInterrupt:
        print("Stopping Scene Engine. Waiting for running scenes to finish...")
        shutdown_event.set()
        mqtt_client.disconnect()
        # Cho phép các luồng pulse đang sleep có 2 giây để gửi lệnh OFF
        time.sleep(2)
    except Exception as e:
        print(f"MQTT Connection failed: {e}")
