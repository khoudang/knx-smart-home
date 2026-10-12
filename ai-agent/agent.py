import os
import sys
import json
from dotenv import load_dotenv
from openai import OpenAI

from tools import (
    get_devices, get_device_state, set_device_state, propose_scene, query_history,
    get_weather, web_search, convert_currency, calculate, get_stock_price,
    get_datetime_info, translate_text, add_device, remove_device, confirm_remove_device
)

load_dotenv()

# Lấy API key từ .env, không hardcode.
api_key = os.environ.get("DEEPSEEK_API_KEY")

if not api_key:
    print("WARNING: Đang chờ Deepseek API key...")

# Khởi tạo client OpenAI-compatible cho Deepseek (chỉ khi có key)
client = OpenAI(
    api_key=api_key,
    base_url="https://api.deepseek.com/v1"
) if api_key else None

# Deepseek model hỗ trợ tool calling là deepseek-chat
MODEL_NAME = "deepseek-chat"

# --- Định nghĩa Schema cho API ---
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_devices",
            "description": "Lấy danh sách các thiết bị trong nhà thông minh và trạng thái hiện tại. Dùng khi cần tìm ID của thiết bị để điều khiển. Có thể lọc theo phòng (room) hoặc loại (type). Các phòng: living_room, bedroom, gate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {
                        "type": "string",
                        "description": "Tên phòng (bắt buộc phải là ID tiếng Anh, VD: living_room, bedroom, gate). Nếu không có, bỏ trống."
                    },
                    "type": {
                        "type": "string",
                        "description": "Loại thiết bị (VD: light, ac, curtain, sensor, gate, lock). Nếu không có, bỏ trống."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_device_state",
            "description": "Lấy trạng thái chi tiết của một thiết bị cụ thể thông qua device_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "ID của thiết bị cần xem trạng thái (VD: lr_light_main, br_ac)."
                    }
                },
                "required": ["device_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_device_state",
            "description": "Gửi lệnh điều khiển thay đổi trạng thái của một thiết bị thực (Bật/Tắt, chỉnh nhiệt độ...). Gây ra side-effect.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "ID của thiết bị cần điều khiển (VD: lr_light_main)."
                    },
                    "action": {
                        "type": "string",
                        "description": "Loại hành động, thông thường luôn là 'set'."
                    },
                    "params": {
                        "type": "object",
                        "description": "Tham số điều khiển. Ví dụ để bật/tắt: {'state': 'ON'} hoặc {'state': 'OFF'}. Để đặt nhiệt độ: {'temperature_setpoint': 24}."
                    }
                },
                "required": ["device_id", "action", "params"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "propose_scene",
            "description": "Tạo một ngữ cảnh (scene) điều khiển nhiều thiết bị cùng lúc. Ngữ cảnh này sẽ được lưu ở trạng thái nháp (draft). Gây ra side-effect.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Tên ngữ cảnh, ngắn gọn, dễ hiểu."
                    },
                    "description": {
                        "type": "string",
                        "description": "Mô tả chi tiết mục đích của ngữ cảnh."
                    },
                    "actions": {
                        "type": "array",
                        "description": "Danh sách các hành động điều khiển thiết bị trong ngữ cảnh này.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "device_id": {"type": "string"},
                                "action_type": {"type": "string", "description": "'set' (lệnh tĩnh) hoặc 'pulse' (nháy thiết bị một thời gian rồi tắt)"},
                                "params": {"type": "object"}
                            },
                            "required": ["device_id", "action_type", "params"]
                        }
                    }
                },
                "required": ["name", "description", "actions"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_history",
            "description": "Truy vấn lịch sử điều khiển, lịch sử thay đổi trạng thái của thiết bị để tìm hiểu thói quen của người dùng.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {"type": "string", "description": "Lọc theo phòng (VD: living_room, bedroom)."},
                    "device_id": {"type": "string", "description": "Lọc theo thiết bị cụ thể."},
                    "limit": {"type": "integer", "description": "Số lượng bản ghi tối đa trả về (mặc định 20)."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Lấy thông tin thời tiết hiện tại của một địa điểm cụ thể.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "Tên thành phố hoặc địa điểm (VD: Hanoi, Ho Chi Minh)."}
                },
                "required": ["location"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Tìm kiếm thông tin trên web (dùng cho các câu hỏi cần dữ liệu thực tế, tin tức mới nhất, hoặc kiến thức nằm ngoài hệ thống).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Câu truy vấn tìm kiếm."},
                    "max_results": {"type": "integer", "description": "Số lượng kết quả trả về (mặc định 3)."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "convert_currency",
            "description": "Chuyển đổi tiền tệ giữa hai loại tiền (VD: USD sang VND).",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "Số tiền cần chuyển đổi."},
                    "from_currency": {"type": "string", "description": "Mã tiền tệ gốc (VD: USD, EUR, VND)."},
                    "to_currency": {"type": "string", "description": "Mã tiền tệ đích (VD: VND, JPY)."}
                },
                "required": ["amount", "from_currency", "to_currency"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Tính toán một biểu thức toán học an toàn.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Biểu thức toán học (VD: '15 * (20 + 3)', '2**10')."}
                },
                "required": ["expression"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_price",
            "description": "Lấy giá cổ phiếu hoặc crypto hiện tại theo mã chứng khoán (VD: AAPL, BTC-USD).",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Mã chứng khoán hoặc crypto (VD: TSLA, GOOG, BTC-USD)."}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_datetime_info",
            "description": "Lấy ngày giờ hiện tại, có thể xem giờ của một múi giờ cụ thể.",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {"type": "string", "description": "Múi giờ (VD: Asia/Ho_Chi_Minh, America/New_York). Bỏ trống để lấy giờ hệ thống/địa phương."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "translate_text",
            "description": "Dịch một đoạn văn bản sang ngôn ngữ đích.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Văn bản cần dịch."},
                    "target_language": {"type": "string", "description": "Ngôn ngữ đích (VD: en, vi, fr, ja)."}
                },
                "required": ["text", "target_language"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_device",
            "description": "Thêm một thiết bị mới vào cơ sở dữ liệu. Chỉ dành cho quản trị viên (owner). Gây ra side-effect.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "ID thiết bị (VD: kitchen_light)."},
                    "room": {"type": "string", "description": "Phòng chứa thiết bị (VD: kitchen, living_room)."},
                    "type": {"type": "string", "description": "Loại thiết bị (VD: light, ac, curtain)."},
                    "group_address": {"type": "string", "description": "Địa chỉ group KNX (VD: 1/1/1)."},
                    "capabilities": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Danh sách chức năng (VD: ['on_off', 'brightness'])."
                    },
                    "is_simulated": {"type": "boolean", "description": "Là thiết bị giả lập (true) hay thiết bị thật (false). Mặc định là true nếu không nói rõ."}
                },
                "required": ["id", "room", "type", "group_address", "capabilities"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remove_device",
            "description": "Xóa một thiết bị khỏi hệ thống (BƯỚC 1). Công cụ này chỉ ĐÁNH DẤU thiết bị là đang chờ xóa (pending_delete) để đảm bảo an toàn. Bạn phải yêu cầu người dùng xác nhận lại trước khi gọi confirm_remove_device. Gây ra side-effect.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "ID thiết bị cần xóa (VD: kitchen_light)."}
                },
                "required": ["device_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_remove_device",
            "description": "Xác nhận xóa hoàn toàn thiết bị (BƯỚC 2). CHỈ gọi công cụ này SAU KHI người dùng đã nói rõ họ chắc chắn muốn xóa ở lượt chat trước. Gây ra side-effect.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "ID thiết bị cần xóa hoàn toàn (VD: kitchen_light)."}
                },
                "required": ["device_id"]
            }
        }
    }
]

# --- Logic Xử lý Lệnh (Map) ---
def process_command(prompt: str, role: str = 'guest'):
    print(f"\n[USER] {prompt} (Role: {role})")

    if not client:
        return "Tôi đang chờ Deepseek API key được cấu hình trong .env."

    system_prompt = """Bạn là một trợ lý AI đa năng và thông minh.
Nhiệm vụ của bạn là hỗ trợ người dùng điều khiển nhà thông minh, đồng thời bạn cũng CÓ THỂ trò chuyện, giải đáp kiến thức chung, trả lời các câu hỏi ngoài phạm vi nhà thông minh một cách bình thường như một AI thông thường. Đừng bao giờ từ chối trả lời kiến thức chung.
Lưu ý quan trọng khi xử lý lệnh NHÀ THÔNG MINH:
1. Luôn truy vấn danh sách thiết bị (get_devices) để lấy đúng 'device_id' trước khi thực thi lệnh set_device_state nếu bạn chưa chắc chắn ID của thiết bị.
2. Các phòng thường dùng: living_room (phòng khách), bedroom (phòng ngủ), gate (cổng).
3. Đừng tự ý bịa ra device_id hoặc room.
4. XÓA THIẾT BỊ LÀ QUY TRÌNH 2 BƯỚC: Nếu người dùng yêu cầu xóa thiết bị, bạn phải gọi `remove_device` trước (để đánh dấu pending_delete), sau đó THÔNG BÁO cho người dùng và YÊU CẦU XÁC NHẬN (chưa gọi confirm ngay trong lượt này). CHỈ KHI người dùng xác nhận ở tin nhắn SAU (ví dụ "đúng, xóa luôn"), bạn mới được gọi `confirm_remove_device`.
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt}
    ]

    # Filter tools based on role
    active_tools = []
    read_only_tools = ['get_devices', 'get_device_state', 'query_history', 'get_weather', 'web_search', 'convert_currency', 'calculate', 'get_stock_price', 'get_datetime_info', 'translate_text']
    for tool in TOOLS_SCHEMA:
        tool_name = tool['function']['name']
        if role == 'guest':
            if tool_name in read_only_tools:
                active_tools.append(tool)
        else:
            active_tools.append(tool)

    # If no tools available (shouldn't happen, but safe fallback)
    if not active_tools:
        active_tools = None

    MAX_ITERATIONS = 5
    iteration = 0

    try:
        while iteration < MAX_ITERATIONS:
            iteration += 1

            # Gọi LLM (luôn kèm tools)
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                tools=active_tools,
                tool_choice="auto" if active_tools else "none"
            )

            response_message = response.choices[0].message
            messages.append(response_message)

            # Nếu không có tool_call nào, đó là câu trả lời cuối
            if not response_message.tool_calls:
                print(f"\n[AI] {response_message.content}")
                return response_message.content

            # Xử lý các tool calls
            for tool_call in response_message.tool_calls:
                tool_name = tool_call.function.name
                tool_id = tool_call.id

                try:
                    tool_inputs = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    tool_inputs = {}

                # --- SAFETY LOGGING CHO SIDE-EFFECT ---
                allowed_tool_names = [t['function']['name'] for t in (active_tools or [])]
                if tool_name not in allowed_tool_names:
                    result = {"error": f"Tool '{tool_name}' không được phép với quyền hiện tại của bạn."}
                    print(f"\n[AGENT] Tool bị chặn do phân quyền: {tool_name}")
                else:
                    if tool_name in ["set_device_state", "propose_scene", "add_device", "remove_device", "confirm_remove_device"]:
                        print(f"\n[SAFETY LOG] AI sắp thực hiện: {tool_name}")
                        print(f"[SAFETY LOG] Params: {json.dumps(tool_inputs, ensure_ascii=False)}")
                    else:
                        print(f"\n[AGENT] Đang sử dụng tool: {tool_name}({tool_inputs})")

                    # --- THỰC THI TOOL ---
                    try:
                        if tool_name == "get_devices":
                            result = get_devices(**tool_inputs)
                        elif tool_name == "get_device_state":
                            result = get_device_state(**tool_inputs)
                        elif tool_name == "set_device_state":
                            result = set_device_state(**tool_inputs)
                        elif tool_name == "propose_scene":
                            result = propose_scene(**tool_inputs)
                        elif tool_name == "query_history":
                            result = query_history(**tool_inputs)
                        elif tool_name == "get_weather":
                            result = get_weather(**tool_inputs)
                        elif tool_name == "web_search":
                            result = web_search(**tool_inputs)
                        elif tool_name == "convert_currency":
                            result = convert_currency(**tool_inputs)
                        elif tool_name == "calculate":
                            result = calculate(**tool_inputs)
                        elif tool_name == "get_stock_price":
                            result = get_stock_price(**tool_inputs)
                        elif tool_name == "get_datetime_info":
                            result = get_datetime_info(**tool_inputs)
                        elif tool_name == "translate_text":
                            result = translate_text(**tool_inputs)
                        elif tool_name == "add_device":
                            result = add_device(**tool_inputs)
                        elif tool_name == "remove_device":
                            result = remove_device(**tool_inputs)
                        elif tool_name == "confirm_remove_device":
                            result = confirm_remove_device(**tool_inputs)
                        else:
                            result = {"error": f"Unknown tool: {tool_name}"}
                    except Exception as e:
                        result = {"error": str(e)}

                    print(f"[AGENT] Tool result: {json.dumps(result, ensure_ascii=False)[:200]}...")

                # Gửi kết quả lại cho LLM
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": tool_name,
                    "content": json.dumps(result, ensure_ascii=False)
                })

        print("\n[ERROR] Đạt giới hạn lặp công cụ tối đa (5 vòng). Ngừng xử lý.")
        return "Xin lỗi, câu hỏi này quá phức tạp nên tôi không thể xử lý xong."

    except Exception as e:
        print(f"\n[ERROR] Gọi API thất bại: {e}")
        return str(e)

# --- Các câu test tự động ---
TEST_PROMPTS = [
    # Old tests (regression)
    "Cho tôi biết phòng khách đang có những thiết bị nào?",
    "Kiểm tra xem đèn chính phòng ngủ đang bật hay tắt.",
    "Bật điều hòa phòng khách lên 24 độ giúp tôi.",
    "Tạo cho tôi một scene 'Đi làm' tắt hết đèn phòng khách và đóng rèm phòng khách.",
    "Hôm nay có ai điều khiển rèm phòng khách không, cho tôi xem lịch sử?",

    # Specific bug from OpenClaw
    "Tắt đèn phòng khách",

    # New tool tests
    "Thời tiết ở Hồ Chí Minh thế nào?",
    "Giá cổ phiếu của AAPL hôm nay là bao nhiêu?",
    "15 * (20 + 3) bằng bao nhiêu?",
    "10 USD bằng bao nhiêu VND?",
    "Hôm nay là thứ mấy và mấy giờ rồi ở New York?",
    "Dịch câu 'Tôi là trợ lý ảo' sang tiếng Anh.",
    "Tin tức mới nhất về AI hôm nay"
]

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Nhận lệnh từ command line
        user_input = " ".join(sys.argv[1:])
        process_command(user_input, role='owner')
    else:
        # Chạy chế độ test tự động
        print("Không có tham số dòng lệnh. Đang chạy chế độ tự động với 5 câu test mẫu...")
        for prompt in TEST_PROMPTS:
            process_command(prompt, role='owner')
            print("-" * 60)
