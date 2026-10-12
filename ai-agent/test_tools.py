import json
import time
import threading
import paho.mqtt.client as mqtt
from tools import get_devices, get_device_state, set_device_state, propose_scene, query_history, get_db_connection

def print_result(name, result):
    print(f"\n{'='*50}\n[TEST] {name}\n{'='*50}")
    print(json.dumps(result, indent=2, ensure_ascii=False))

def cleanup(scene_id):
    conn = get_db_connection()
    try:
        conn.run("DELETE FROM scenes WHERE id = :id", id=scene_id)
        print(f"Cleaned up test scene {scene_id}")
    finally:
        conn.close()

mqtt_received = []
def on_message(client, userdata, msg):
    mqtt_received.append((msg.topic, msg.payload.decode()))

def run_tests():
    print("Bắt đầu chạy Test Độc lập cho AI Tools (Phase 4)...")

    # Setup MQTT Subscriber for verification
    client = mqtt.Client(client_id="ai-agent-tester")
    client.on_message = on_message
    client.connect("localhost", 1883, 60)
    client.subscribe("cmd/lr_light_main/power/set")
    client.loop_start()

    # Test 1: get_devices
    devices_all = get_devices()
    print_result("get_devices() - ALL", devices_all)
    assert len(devices_all) == 10, f"Expected 10 devices, got {len(devices_all)}"

    devices_lr = get_devices(room="living_room")
    print_result("get_devices(room='living_room')", devices_lr)

    # Test 2: get_device_state
    if devices_lr:
        dev_id = devices_lr[0]["device_id"]
        state = get_device_state(dev_id)
        print_result(f"get_device_state('{dev_id}')", state)

        # Test missing device
        bad_state = get_device_state("non_existent_id")
        print_result("get_device_state('non_existent_id')", bad_state)

    # Test 3: set_device_state (Turn ON living room main light)
    set_res = set_device_state("lr_light_main", "set", {"state": "ON"})
    print_result("set_device_state('lr_light_main', 'set', {'state': 'ON'})", set_res)
    time.sleep(1) # wait for mqtt message to arrive
    print_result("MQTT Messages Received", mqtt_received)
    assert len(mqtt_received) > 0, "No MQTT message was received on topic cmd/lr_light_main/power/set!"

    # Test 4: propose_scene
    actions = [
        {"device_id": "lr_light_main", "action_type": "set", "params": {"state": "OFF"}},
        {"device_id": "br_light_pulse", "action_type": "pulse", "params": {"on_duration_ms": 3000}}
    ]
    scene_res = propose_scene("AI Tắt đèn & Nháy nháy", "AI Agent đề xuất khi người dùng muốn đi ngủ", actions)
    print_result("propose_scene()", scene_res)
    assert "scene_id" in scene_res, "propose_scene failed to return scene_id"
    scene_id = scene_res["scene_id"]

    # Check DB for scene
    conn = get_db_connection()
    try:
        rows = conn.run("SELECT id, status FROM scenes WHERE id = :id", id=scene_id)
        print_result("Query newly created scene", rows)
        assert len(rows) == 1 and rows[0][1] == 'draft', "Scene not found or not draft"

        acts = conn.run("SELECT * FROM scene_actions WHERE scene_id = :id", id=scene_id)
        print_result("Query scene actions", acts)
        assert len(acts) == 2, "Expected 2 actions"
    finally:
        conn.close()

    # Test 5: query_history
    hist_all = query_history(limit=5)
    print_result("query_history(limit=5)", hist_all)

    hist_scene = query_history(device_id="lr_light_main", limit=2)
    print_result("query_history(device_id='lr_light_main', limit=2)", hist_scene)

    # Cleanup
    cleanup(scene_id)
    client.loop_stop()
    print("\nHoàn thành Test AI Tools!")

if __name__ == "__main__":
    run_tests()
