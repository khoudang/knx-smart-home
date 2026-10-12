import json
import os
if not os.environ.get("KNX_IP"):
    raise SystemExit("Set KNX_IP before generating local configuration")

with open("config/devices_cache.json", "r") as f:
    devices = json.load(f)

knx_config = {
    "knxConnection": {
        "ipAddr": os.environ["KNX_IP"],
        "ipPort": 3671
    },
    "accessories": []
}

for dev in devices:
    if str(dev.get("id")).startswith("den_phong_ai_"):
        continue # Bỏ qua scene tạm thời vì KNX plugin xử lý scene khác

    acc = {
        "AccessoryName": dev["name"],
        "AccessoryType": "Lightbulb",
        "Services": [
            {
                "ServiceType": "Lightbulb",
                "ServiceName": dev["name"],
                "Characteristics": []
            }
        ]
    }

    chars = acc["Services"][0]["Characteristics"]

    # Bật/Tắt
    if dev.get("control") or dev.get("gaOn"):
        char_on = {
            "Type": "On",
            "Set": [dev.get("control") or dev.get("gaOn")]
        }
        if dev.get("status"):
            char_on["Listen"] = [dev["status"]]
        elif dev.get("control"):
            char_on["Listen"] = [dev["control"]]
        chars.append(char_on)

    # Độ sáng (Dim)
    if dev.get("dim"):
        char_dim = {
            "Type": "Brightness",
            "Set": [dev["dim"]]
        }
        if dev.get("dimStatus"):
            char_dim["Listen"] = [dev["dimStatus"]]
        chars.append(char_dim)

    if chars:
        knx_config["accessories"].append(acc)

with open("knx_config.json", "w", encoding="utf-8") as f:
    json.dump(knx_config, f, indent=4, ensure_ascii=False)

print("Generated knx_config.json")
