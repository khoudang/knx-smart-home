import os
import json
import uuid
import sys
from pathlib import Path
from datetime import datetime
import pg8000.native
import paho.mqtt.publish as mqtt_publish
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

# --- DB Helper ---
def get_db_connection():
    return pg8000.native.Connection(user=DB_USER, password=DB_PASS, host=DB_HOST, database=DB_NAME)

# --- Tool Functions ---

def get_devices(room=None, type=None) -> list:
    """
    Query bảng devices (và join device_states) để lấy danh sách thiết bị và trạng thái hiện tại.
    """
    conn = get_db_connection()
    try:
        query = """
            SELECT d.id, d.room, d.type, d.capabilities, s.state
            FROM devices d
            LEFT JOIN device_states s ON d.id = s.device_id
            WHERE 1=1
        """
        params = {}
        if room:
            query += " AND d.room = :room"
            params['room'] = room
        if type:
            query += " AND d.type = :type"
            params['type'] = type

        rows = conn.run(query, **params)

        devices = []
        for r in rows:
            devices.append({
                "device_id": r[0],
                "room": r[1],
                "type": r[2],
                "capabilities": r[3],
                "state": r[4] if r[4] else {}
            })
        return devices
    finally:
        conn.close()

def get_device_state(device_id: str) -> dict:
    """
    Lấy trạng thái chi tiết của một thiết bị.
    """
    conn = get_db_connection()
    try:
        rows = conn.run("""
            SELECT s.state, s.updated_at
            FROM device_states s
            WHERE s.device_id = :device_id
        """, device_id=device_id)

        if not rows:
            return {"error": f"Device {device_id} not found or has no state."}

        return {
            "device_id": device_id,
            "state": rows[0][0],
            "updated_at": rows[0][1].isoformat() if rows[0][1] else None
        }
    finally:
        conn.close()

def set_device_state(device_id: str, action: str, params: dict) -> dict:
    """
    Gửi lệnh điều khiển MQTT đến thiết bị cụ thể.
    """
    conn = get_db_connection()
    try:
        # Lấy thông tin room để build MQTT topic
        rows = conn.run("SELECT room FROM devices WHERE id = :device_id", device_id=device_id)
        if not rows:
            return {"error": f"Device {device_id} not found."}

        capability, value = _params_to_contract(params)
        topic = command_topic(device_id, capability)
        payload_obj = build_command_payload(str(uuid.uuid4()), device_id, capability, value)
        payload = json.dumps(payload_obj)

        # Publish MQTT message (fire and forget)
        mqtt_publish.single(topic, payload=payload, hostname=MQTT_HOST, port=MQTT_PORT)

        return {
            "status": "sent",
            "topic": topic,
            "payload": payload_obj
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def _params_to_contract(params: dict) -> tuple[str, object]:
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

def propose_scene(name: str, description: str, actions: list) -> dict:
    """
    Tạo một ngữ cảnh mới (Draft) vào DB dựa theo yêu cầu.
    actions là list of dict: [{"device_id": "...", "action_type": "set", "params": {...}}, ...]
    """
    conn = get_db_connection()
    try:
        conn.run("BEGIN")

        # Sinh ID cho scene
        scene_id = "scene_" + str(uuid.uuid4())[:8]

        # Insert vào bảng scenes với status='draft'
        conn.run("""
            INSERT INTO scenes (id, name, description, trigger_type, status)
            VALUES (:id, :name, :description, 'manual', 'draft')
        """, id=scene_id, name=name, description=description)

        # Insert các hành động tương ứng
        for idx, act in enumerate(actions):
            conn.run("""
                INSERT INTO scene_actions (scene_id, device_id, action_type, params, action_order)
                VALUES (:scene_id, :device_id, :action_type, CAST(:params AS JSONB), :order)
            """,
            scene_id=scene_id,
            device_id=act['device_id'],
            action_type=act['action_type'],
            params=json.dumps(act['params']),
            order=idx+1)

        conn.run("COMMIT")
        return {
            "status": "proposed",
            "scene_id": scene_id,
            "message": "Scene drafted successfully. Waiting for user approval."
        }
    except Exception as e:
        conn.run("ROLLBACK")
        return {"error": str(e)}
    finally:
        conn.close()

def query_history(room=None, device_id=None, since=None, limit=20) -> list:
    """
    Truy vấn lịch sử hành động để phân tích thói quen.
    since: ISO format string
    """
    conn = get_db_connection()
    try:
        query = """
            SELECT h.id, h.device_id, d.room, h.actor, h.action, h.params, h.result, h.timestamp
            FROM device_history h
            JOIN devices d ON h.device_id = d.id
            WHERE 1=1
        """
        params = {}

        if room:
            query += " AND d.room = :room"
            params['room'] = room

        if device_id:
            query += " AND h.device_id = :device_id"
            params['device_id'] = device_id

        if since:
            # since should be parsed as datetime, but pg8000 can handle strings directly or we can let postgres parse it
            query += " AND h.timestamp >= CAST(:since AS TIMESTAMP WITH TIME ZONE)"
            params['since'] = since

        query += " ORDER BY h.timestamp DESC LIMIT :limit"
        params['limit'] = limit

        rows = conn.run(query, **params)

        history = []
        for r in rows:
            history.append({
                "history_id": r[0],
                "device_id": r[1],
                "room": r[2],
                "actor": r[3],
                "action": r[4],
                "params": r[5],
                "result": r[6],
                "timestamp": r[7].isoformat() if r[7] else None
            })
        return history
    finally:
        conn.close()

# --- NEW TOOLS ---
import requests
import simpleeval
import yfinance as yf
import pytz

def get_weather(location: str) -> dict:
    """Lấy thời tiết hiện tại qua Open-Meteo."""
    try:
        # 1. Get coordinates for location using Open-Meteo geocoding API
        geocode_url = f"https://geocoding-api.open-meteo.com/v1/search?name={location}&count=1&language=vi&format=json"
        res = requests.get(geocode_url, timeout=5)
        res.raise_for_status()
        data = res.json()
        if not data.get("results"):
            return {"error": f"Không tìm thấy vị trí '{location}'."}

        lat = data["results"][0]["latitude"]
        lon = data["results"][0]["longitude"]
        name = data["results"][0]["name"]
        country = data["results"][0].get("country", "")

        # 2. Get weather using coordinates
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        w_res = requests.get(weather_url, timeout=5)
        w_res.raise_for_status()
        w_data = w_res.json()

        current = w_data.get("current_weather", {})
        return {
            "location": f"{name}, {country}",
            "temperature": current.get("temperature"),
            "windspeed": current.get("windspeed"),
            "time": current.get("time")
        }
    except Exception as e:
        return {"error": f"Lỗi khi lấy thời tiết: {str(e)}"}

def web_search(query: str, max_results: int = 3) -> dict:
    """Tìm kiếm web qua Tavily API."""
    try:
        api_key = os.environ.get("TAVILY_API_KEY")
        if not api_key:
            return {"error": "TAVILY_API_KEY not configured in environment."}

        url = "https://api.tavily.com/search"
        payload = {
            "api_key": api_key,
            "query": query,
            "max_results": max_results,
            "include_answer": True
        }
        res = requests.post(url, json=payload, timeout=10)
        res.raise_for_status()
        data = res.json()

        results = [{"title": r.get("title"), "url": r.get("url"), "content": r.get("content")} for r in data.get("results", [])]
        return {
            "query": query,
            "answer": data.get("answer"),
            "results": results
        }
    except Exception as e:
        return {"error": f"Lỗi tìm kiếm web: {str(e)}"}

def convert_currency(amount: float, from_currency: str, to_currency: str) -> dict:
    """Chuyển đổi tiền tệ qua exchangerate-api.com (open tier)."""
    try:
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()
        url = f"https://open.er-api.com/v6/latest/{from_currency}"
        res = requests.get(url, timeout=5)
        res.raise_for_status()
        data = res.json()

        if data.get("result") != "success":
            return {"error": "API tiền tệ trả về lỗi."}

        rates = data.get("rates", {})
        if to_currency not in rates:
            return {"error": f"Không hỗ trợ tiền tệ đích: {to_currency}"}

        rate = rates[to_currency]
        return {
            "amount": amount,
            "from": from_currency,
            "to": to_currency,
            "result": amount * rate,
            "rate": rate
        }
    except Exception as e:
        return {"error": f"Lỗi chuyển đổi tiền tệ: {str(e)}"}

def calculate(expression: str) -> dict:
    """Tính toán biểu thức toán học an toàn."""
    try:
        # Sử dụng simpleeval để tránh lỗi eval() injection
        result = simpleeval.simple_eval(expression)
        return {"expression": expression, "result": result}
    except Exception as e:
        return {"error": f"Lỗi tính toán: {str(e)}"}

def get_stock_price(symbol: str) -> dict:
    """Lấy giá cổ phiếu hiện tại qua yfinance."""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        # fast_info is much faster and less prone to rate limits than full info
        return {
            "symbol": symbol.upper(),
            "price": info.last_price,
            "currency": info.currency
        }
    except Exception as e:
        return {"error": f"Không lấy được giá cho {symbol}. Lỗi: {str(e)}"}

def get_datetime_info(timezone: str = None) -> dict:
    """Lấy ngày giờ hiện tại, hỗ trợ múi giờ."""
    try:
        if timezone:
            tz = pytz.timezone(timezone)
            now = datetime.now(tz)
        else:
            now = datetime.now()
            timezone = "Local"

        return {
            "timezone": timezone,
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "day_of_week": now.strftime("%A")
        }
    except Exception as e:
        return {"error": f"Lỗi khi lấy ngày giờ: {str(e)}"}

def translate_text(text: str, target_language: str) -> dict:
    """Dịch văn bản qua MyMemory Translation API."""
    try:
        url = "https://api.mymemory.translated.net/get"
        params = {
            "q": text,
            "langpair": f"vi|{target_language}"  # Assuming input is usually vietnamese, but mymemory auto-detects if you omit, wait, it requires a valid pair. Let's use autodetect if possible.
        }
        # MyMemory supports autodetect via Autodetect|Target
        params["langpair"] = f"Autodetect|{target_language}"
        res = requests.get(url, params=params, timeout=5)
        res.raise_for_status()
        data = res.json()

        if data.get("responseStatus") == 200:
            return {
                "original_text": text,
                "target_language": target_language,
                "translated_text": data["responseData"]["translatedText"]
            }
        else:
            return {"error": data.get("responseDetails", "Translation API error")}
    except Exception as e:
        return {"error": f"Lỗi dịch thuật: {str(e)}"}

def add_device(id: str, room: str, type: str, group_address: str, capabilities: list, is_simulated: bool = True) -> dict:
    """Thêm một thiết bị mới vào database."""
    conn = get_db_connection()
    try:
        # Validate group_address format x/y/z
        parts = group_address.split('/')
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            return {"error": f"group_address '{group_address}' không hợp lệ. Phải có định dạng x/y/z."}

        print(f"\n[SAFETY LOG] AI sắp thực hiện: add_device")
        print(f"[SAFETY LOG] Params: id={id}, room={room}, type={type}")

        conn.run("BEGIN")
        conn.run("""
            INSERT INTO devices (id, room, type, group_address, capabilities, is_simulated, status)
            VALUES (:id, :room, :type, :group_address, CAST(:capabilities AS JSONB), :is_simulated, 'active')
        """, id=id, room=room, type=type, group_address=group_address, capabilities=json.dumps(capabilities), is_simulated=is_simulated)

        # Thêm trạng thái rỗng ban đầu cho thiết bị
        conn.run("""
            INSERT INTO device_states (device_id, state)
            VALUES (:id, '{}'::jsonb)
        """, id=id)

        conn.run("COMMIT")
        return {"status": "success", "message": f"Đã thêm thiết bị {id} thành công."}
    except Exception as e:
        conn.run("ROLLBACK")
        return {"error": str(e)}
    finally:
        conn.close()

def remove_device(device_id: str) -> dict:
    """Đánh dấu thiết bị đang chờ xóa (pending_delete). Bước 1/2."""
    conn = get_db_connection()
    try:
        print(f"\n[SAFETY LOG] AI sắp thực hiện: remove_device (Đánh dấu chờ xóa)")
        print(f"[SAFETY LOG] Params: device_id={device_id}")

        rows = conn.run("UPDATE devices SET status = 'pending_delete' WHERE id = :id RETURNING id", id=device_id)
        if not rows:
            return {"error": f"Không tìm thấy thiết bị {device_id}."}

        return {"status": "pending_delete", "message": f"Thiết bị {device_id} đã được đưa vào trạng thái chờ xóa. Bạn phải gọi tool confirm_remove_device để thực sự xóa."}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()

def confirm_remove_device(device_id: str) -> dict:
    """Xóa hoàn toàn thiết bị. Bước 2/2."""
    conn = get_db_connection()
    try:
        print(f"\n[SAFETY LOG] AI sắp thực hiện: confirm_remove_device (XÓA HOÀN TOÀN)")
        print(f"[SAFETY LOG] Params: device_id={device_id}")

        conn.run("BEGIN")
        # Phải kiểm tra xem đã pending_delete chưa
        rows = conn.run("SELECT status FROM devices WHERE id = :id", id=device_id)
        if not rows:
            conn.run("ROLLBACK")
            return {"error": f"Không tìm thấy thiết bị {device_id}."}
        if rows[0][0] != 'pending_delete':
            conn.run("ROLLBACK")
            return {"error": f"Thiết bị {device_id} chưa ở trạng thái chờ xóa. Vui lòng gọi remove_device trước."}

        # Xóa các tham chiếu (device_states, history... cascade)
        conn.run("DELETE FROM device_states WHERE device_id = :id", id=device_id)
        conn.run("DELETE FROM device_history WHERE device_id = :id", id=device_id)
        # Bỏ qua scene_actions nếu có foreign key cascade, nếu không thì tự xóa
        conn.run("DELETE FROM scene_actions WHERE device_id = :id", id=device_id)

        conn.run("DELETE FROM devices WHERE id = :id", id=device_id)

        conn.run("COMMIT")
        return {"status": "deleted", "message": f"Thiết bị {device_id} đã bị xóa hoàn toàn khỏi hệ thống."}
    except Exception as e:
        conn.run("ROLLBACK")
        return {"error": str(e)}
    finally:
        conn.close()
