import json
import os
if not os.environ.get("SIRI_SECRET_TOKEN"):
    raise SystemExit("Set SIRI_SECRET_TOKEN before generating local Homebridge config")

with open("config/devices_cache.json", "r") as f:
    devices = json.load(f)

hb_accessories = []
for dev in devices:
    if not dev.get("control"): continue
    acc = {
        "accessory": "HTTP-SWITCH",
        "name": dev["name"],
        "switchType": "stateful",
        "onUrl": {
            "url": "http://127.0.0.1:8000/siri/control",
            "method": "POST",
            "headers": {
                "Authorization": "Bearer " + os.environ["SIRI_SECRET_TOKEN"],
                "Content-Type": "application/json"
            },
            "body": json.dumps({"device_id": dev["id"], "ga": dev["control"], "command": "on"})
        },
        "offUrl": {
            "url": "http://127.0.0.1:8000/siri/control",
            "method": "POST",
            "headers": {
                "Authorization": "Bearer " + os.environ["SIRI_SECRET_TOKEN"],
                "Content-Type": "application/json"
            },
            "body": json.dumps({"device_id": dev["id"], "ga": dev["control"], "command": "off"})
        }
    }
    hb_accessories.append(acc)

with open("homebridge_accessories.json", "w", encoding="utf-8") as f:
    json.dump(hb_accessories, f, indent=4, ensure_ascii=False)
print("Generated homebridge_accessories.json")
