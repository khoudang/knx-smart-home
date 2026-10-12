import asyncio
import sys
import json
import os
import subprocess
import datetime
from xknx import XKNX
from xknx.devices import Switch, Light
from xknx.io import ConnectionConfig, ConnectionType
from xknx.telegram import Telegram, GroupAddress
from xknx.telegram.apci import GroupValueWrite, GroupValueRead, GroupValueResponse
from xknx.dpt import DPTColorTemperature

STATE_FILE = '/tmp/knx_device_states.json'
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(BASE_DIR, 'logs', 'knx_history.jsonl')
APPEND_HISTORY_SCRIPT = os.path.join(BASE_DIR, 'skills', 'append_knx_history.js')


def load_states():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {}


def save_state(device_id, state):
    states = load_states()
    states[device_id] = state
    with open(STATE_FILE, 'w') as f:
        json.dump(states, f)


def append_history_local(record):
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


def append_history_sheet(record):
    if os.environ.get('KNX_HISTORY_SKIP_SHEET') == '1':
        return
    if not os.path.exists(APPEND_HISTORY_SCRIPT):
        return
    try:
        subprocess.run(
            ['node', APPEND_HISTORY_SCRIPT, json.dumps(record, ensure_ascii=False)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        pass


def record_history(device_id, ga, command, value, status, reason=None, user_name='Hệ thống'):
    now = datetime.datetime.now().astimezone()
    record = {
        'timestamp': now.astimezone(datetime.timezone.utc).isoformat(),
        'localDate': now.strftime('%Y-%m-%d'),
        'time': now.strftime('%H:%M:%S'),
        'user_name': user_name,
        'device_id': device_id,
        'ga': ga,
        'command': command.upper(),
        'value': value,
        'status': status,
        'reason': reason,
        'source': 'middleware.py',
    }
    append_history_local(record)
    append_history_sheet(record)

def get_knx_ip():
    try:
        ip_file = os.path.join(os.path.dirname(__file__), 'config', 'knx_ip.txt')
        with open(ip_file, 'r') as f:
            return f.read().strip()
    except Exception:
        return os.environ.get('KNX_IP', '')

KNX_IP = get_knx_ip()
KNX_PORT = 3671

CACHE_FILE = os.path.join(os.path.dirname(__file__), 'config', 'devices_cache.json')
SCENES_FILE = os.path.join(os.path.dirname(__file__), 'config', 'scenes.json')


def load_devices():
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def load_scenes():
    try:
        with open(SCENES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def normalize_scene_name(device_id, ga, value):
    for raw in (value, ga, device_id):
        if not raw:
            continue
        candidate = str(raw).strip().lower()
        if not candidate:
            continue
        if candidate.startswith('scene:'):
            candidate = candidate.split(':', 1)[1].strip()
        if candidate.startswith('scene_'):
            candidate = candidate[6:].strip()
        if candidate.startswith('scene-'):
            candidate = candidate[6:].strip()
        if candidate == 'scene':
            continue
        return candidate
    return ''


def is_scene_request(device_id, ga, command, value):
    if command.upper() != 'SCENE':
        return False
    scene_name = normalize_scene_name(device_id, ga, value)
    return bool(scene_name)


def validate_ga(device_id, ga, command):
    if command.upper() == 'SCENE':
        return True, ""

    devices = load_devices()
    if devices is None:
        return False, "Missing device registry; refresh local cache before control"

    device = next((d for d in devices if d.get("id") == device_id), None)
    if device is None:
        return False, f"device_id '{device_id}' không tồn tại trong danh sách thiết bị"

    cmd = command.upper()
    allowed_gas = set()

    if cmd == 'ON':
        if device.get('control'):
            allowed_gas.add(device['control'])
        if device.get('gaOn'):
            allowed_gas.add(device['gaOn'])
    elif cmd == 'OFF':
        if device.get('control'):
            allowed_gas.add(device['control'])
        if device.get('gaOff'):
            allowed_gas.add(device['gaOff'])
    elif cmd == 'DIM':
        if device.get('control'):
            allowed_gas.add(device['control'])
        if device.get('dim'):
            allowed_gas.add(device['dim'])
    elif cmd == 'COLOR':
        if device.get('color'):
            allowed_gas.add(device['color'])
    elif cmd == 'READ':
        for key in ('status', 'dimStatus', 'colorStatus'):
            if device.get(key):
                allowed_gas.add(device[key])
    else:
        return False, f"command '{command}' không hợp lệ"

    if not allowed_gas:
        return False, f"thiết bị '{device_id}' không có GA hợp lệ cho lệnh {cmd}"

    if ga not in allowed_gas:
        return False, f"GA '{ga}' không khớp dữ liệu thật của '{device_id}' (hợp lệ: {allowed_gas})"

    return True, ""


async def send_single_knx(xknx, ga, command, value=None, device_id=None):
    cmd = command.upper()

    if cmd in ('ON', 'OFF'):
        if device_id in ('den_phong_ai_1', 'den_phong_ai_2'):
            # Scene pulse: gửi xung ON → 3s → OFF
            switch = Switch(xknx, name="Scene", group_address=ga)
            await switch.set_on()
            await asyncio.sleep(3)
            await switch.set_off()
            if device_id:
                save_state(device_id, {'command': cmd, 'ga': ga, 'value': cmd == 'ON', 'mode': 'scene_pulse'})
            print(f"✅ {cmd} → {ga}")
        else:
            switch = Switch(xknx, name="Control", group_address=ga)
            if cmd == 'ON':
                await switch.set_on()
            else:
                await switch.set_off()
            if device_id:
                save_state(device_id, {'command': cmd, 'ga': ga, 'value': cmd == 'ON'})
            print(f"✅ {cmd} → {ga}")

    elif cmd == 'DIM':
        if device_id:
            devices = load_devices()
            dev = next((d for d in devices if d.get("id") == device_id), None) if devices else None
            if dev and dev.get('control'):
                switch = Switch(xknx, name="Control", group_address=dev['control'])
                await switch.set_on()
                print(f"⏫ Tự động bật nguồn {dev['control']} trước khi DIM")
                await asyncio.sleep(0.5)

        light = Light(xknx, name="Light", group_address_switch=ga, group_address_brightness=ga)
        brightness = max(0, min(255, int(value)))
        await light.set_brightness(brightness)
        if device_id:
            save_state(device_id, {'command': 'DIM', 'ga': ga, 'value': brightness})
        print(f"✅ DIM {brightness} → {ga}")

    elif cmd == 'COLOR':
        if device_id:
            devices = load_devices()
            dev = next((d for d in devices if d.get("id") == device_id), None) if devices else None
            if dev and dev.get('control'):
                switch = Switch(xknx, name="Control", group_address=dev['control'])
                await switch.set_on()
                print(f"⏫ Tự động bật nguồn {dev['control']} trước khi COLOR")
                await asyncio.sleep(0.5)

        requested_kelvin = int(value)
        inverted_kelvin = 2700 + 6500 - requested_kelvin
        inverted_kelvin = max(2700, min(6500, inverted_kelvin))
        encoded = DPTColorTemperature.to_knx(inverted_kelvin)
        telegram = Telegram(
            destination_address=GroupAddress(ga),
            payload=GroupValueWrite(encoded)
        )
        await xknx.telegrams.put(telegram)
        print(f"✅ COLOR {requested_kelvin}K (gửi đảo={inverted_kelvin}K) → {ga}")

    elif cmd == 'READ':
        result_holder = {}
        event = asyncio.Event()

        def on_telegram(telegram):
            if str(telegram.destination_address) == ga and isinstance(telegram.payload, GroupValueResponse):
                result_holder['payload'] = telegram.payload
                event.set()

        xknx.telegram_queue.register_telegram_received_cb(on_telegram)

        read_telegram = Telegram(
            destination_address=GroupAddress(ga),
            payload=GroupValueRead()
        )
        await xknx.telegrams.put(read_telegram)

        try:
            await asyncio.wait_for(event.wait(), timeout=5.0)
            payload = result_holder['payload']
            raw_value = payload.value.value
            if isinstance(raw_value, int):
                print(f"✅ READ {ga} → raw=[{raw_value}] (1-bit)")
                if device_id:
                    save_state(device_id, {'command': 'READ', 'ga': ga, 'raw': raw_value})
            else:
                print(f"✅ READ {ga} → raw={list(raw_value)}")
                if device_id:
                    save_state(device_id, {'command': 'READ', 'ga': ga, 'raw': list(raw_value)})
        except asyncio.TimeoutError:
            print(f"⚠️ READ {ga} → timeout, không có phản hồi sau 5s")
            if device_id:
                print(f"ℹ️ Thiết bị {device_id} không hỗ trợ READ hoặc đang offline")
            else:
                print(f"ℹ️ Thiết bị không hỗ trợ READ hoặc đang offline")

    else:
        raise ValueError(f"Lệnh không hợp lệ: {command}")


async def connect_xknx(max_retries=3):
    if os.environ.get("KNX_DRY_RUN", "true").lower() not in {"0", "false", "no"}:
        raise RuntimeError("Legacy live KNX access is disabled; set KNX_DRY_RUN=false explicitly")
    if not KNX_IP:
        raise RuntimeError("Set KNX_IP or a local config/knx_ip.txt")
    for attempt in range(1, max_retries + 1):
        xknx = XKNX(connection_config=ConnectionConfig(
            connection_type=ConnectionType.TUNNELING,
            gateway_ip=KNX_IP,
            gateway_port=KNX_PORT,
            local_ip=os.environ.get("KNX_LOCAL_IP") or None
        ))
        try:
            await xknx.start()
            return xknx
        except Exception as e:
            print(f"⚠️ Kết nối KNX thất bại (lần {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                await asyncio.sleep(1)
    raise ConnectionError(f"Mất kết nối KNX Gateway ({KNX_IP}). Vui lòng kiểm tra tủ điện/mạng LAN!")


async def send_knx(ga, command, value=None, device_id=None):
    xknx = await connect_xknx()

    try:
        await send_single_knx(xknx, ga, command, value, device_id)
        await asyncio.sleep(0.5)  # Chờ gói tin bay đi trước khi ngắt mạng
    finally:
        await xknx.stop()


async def run_scene(scene_name):
    scenes = load_scenes()
    scene = scenes.get(scene_name)
    if not scene:
        available = ', '.join(sorted(scenes.keys())) or 'all_off, home, night'
        raise ValueError(f"scene '{scene_name}' không tồn tại. Có sẵn: {available}")

    steps = scene.get('steps', []) if isinstance(scene, dict) else scene
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"scene '{scene_name}' không có bước thực thi")

    devices = load_devices() or []
    xknx = await connect_xknx()

    try:
        print(f"🎬 SCENE {scene_name} — bắt đầu")
        for step in steps:
            if not isinstance(step, dict):
                continue

            scope = str(step.get('scope') or '').strip().lower()
            step_command = str(step.get('command') or '').strip().upper()
            step_value = step.get('value')
            step_device_id = str(step.get('device_id') or '').strip()

            if scope == 'all':
                for dev in devices:
                    dev_id = dev.get('id')
                    if not dev_id:
                        continue
                    if str(dev_id).startswith('den_phong_ai_'):
                        continue
                    if step_command in ('ON', 'OFF') and not (dev.get('control') or dev.get('gaOn') or dev.get('gaOff')):
                        continue
                    ga = dev.get('control')
                    if step_command == 'ON' and not ga:
                        ga = dev.get('gaOn')
                    elif step_command == 'OFF' and not ga:
                        ga = dev.get('gaOff')
                    elif step_command == 'DIM':
                        ga = dev.get('dim') or dev.get('control')
                    elif step_command == 'COLOR':
                        ga = dev.get('color')
                    elif step_command == 'READ':
                        ga = dev.get('status') or dev.get('dimStatus') or dev.get('colorStatus')
                    if not ga:
                        continue
                    await send_single_knx(xknx, ga, step_command, step_value, dev_id)
                continue

            if not step_device_id:
                continue
            dev = next((d for d in devices if d.get('id') == step_device_id), None)
            if not dev:
                print(f"⚠️ SCENE {scene_name}: bỏ qua {step_device_id} (không tìm thấy)")
                continue

            ga = step.get('ga')
            if not ga:
                if step_command == 'ON':
                    ga = dev.get('control') or dev.get('gaOn')
                elif step_command == 'OFF':
                    ga = dev.get('control') or dev.get('gaOff')
                elif step_command == 'DIM':
                    ga = dev.get('dim') or dev.get('control')
                elif step_command == 'COLOR':
                    ga = dev.get('color')
                elif step_command == 'READ':
                    ga = dev.get('status') or dev.get('dimStatus') or dev.get('colorStatus')

            if not ga:
                print(f"⚠️ SCENE {scene_name}: bỏ qua {step_device_id} (không có GA hợp lệ cho {step_command})")
                continue
            await send_single_knx(xknx, ga, step_command, step_value, step_device_id)

        print(f"🎬 SCENE {scene_name} — xong")
    finally:
        await xknx.stop()


def log_call(device_id, ga, command, value, status, user_name='Hệ thống'):
    reason = None
    if isinstance(status, str) and status.startswith("REJECTED: "):
        reason = status.removeprefix("REJECTED: ").strip()
    elif isinstance(status, str) and status.startswith("FAILED: "):
        reason = status.removeprefix("FAILED: ").strip()
    record_history(device_id, ga, command, value, status, reason=reason, user_name=user_name)



def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('device_id')
    parser.add_argument('ga')
    parser.add_argument('command')
    parser.add_argument('value', nargs='?', default=None)
    parser.add_argument('--user', default='Hệ thống')
    args, unknown = parser.parse_known_args()

    device_id = args.device_id
    ga = args.ga
    command = args.command
    value = args.value
    user_name = args.user

    if command.upper() == 'SCENE':
        scene_name = normalize_scene_name(device_id, ga, value)
        if not scene_name:
            log_call(device_id, ga, command, value, status="REJECTED: thiếu tên scene", user_name=user_name)
            print("🚫 Từ chối lệnh: thiếu tên scene")
            sys.exit(2)
        try:
            asyncio.run(run_scene(scene_name))
            log_call(device_id, ga, command, scene_name, status="OK", user_name=user_name)
            return
        except Exception as e:
            log_call(device_id, ga, command, scene_name, status=f"FAILED: {e}", user_name=user_name)
            print(f"❌ Lỗi chạy scene: {e}")
            sys.exit(1)

    ok, reason = validate_ga(device_id, ga, command)
    if not ok:
        log_call(device_id, ga, command, value, status=f"REJECTED: {reason}", user_name=user_name)
        print(f"🚫 Từ chối lệnh: {reason}")
        sys.exit(2)

    try:
        asyncio.run(send_knx(ga, command, value, device_id))
        log_call(device_id, ga, command, value, status="OK", user_name=user_name)
    except Exception as e:
        log_call(device_id, ga, command, value, status=f"FAILED: {e}", user_name=user_name)
        print(f"❌ Lỗi gửi lệnh: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
