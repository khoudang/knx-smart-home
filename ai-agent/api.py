import os
from dotenv import load_dotenv
load_dotenv()
import sys
from pathlib import Path

from flask import Flask, jsonify, request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smart_home_agent.agent_core.orchestrator import Orchestrator, adapter_input_from_payload
from smart_home_agent.services.pipeline_log import configure_intent_pipeline_logging, log_pipeline_event


configure_intent_pipeline_logging(stream=sys.stdout)
app = Flask(__name__)
orchestrator = Orchestrator.from_env()
log_pipeline_event(
    event="intent_pipeline_ready",
    service="smarthome-ai-agent",
    database_path_configured=bool(os.environ.get("DATABASE_PATH")),
    knx_dry_run=os.environ.get("KNX_DRY_RUN", ""),
    mqtt_dry_run=os.environ.get("MQTT_DRY_RUN", ""),
)


@app.route("/health", methods=["GET"])
def health():
    return jsonify(
        {
            "status": "ok",
            "service": "smarthome-ai-agent",
            "knx_dry_run": os.environ.get("KNX_DRY_RUN", ""),
            "mqtt_dry_run": os.environ.get("MQTT_DRY_RUN", ""),
            "database_path_configured": bool(os.environ.get("DATABASE_PATH")),
        }
    )


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    if not data or "message" not in data:
        return jsonify({"error": "Missing 'message' in request body"}), 400

    try:
        adapter_input = adapter_input_from_payload(data)
        result = orchestrator.handle(adapter_input)
        return jsonify(
            {
                "reply": result.reply,
                "status": result.status,
                "request_id": result.request_id,
                "executed": result.executed,
                "notifications": result.notifications or [],
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/get_devices", methods=["POST"])
def api_get_devices():
    return jsonify({"error": "legacy direct tool endpoint disabled; use /chat orchestrator"}), 410


@app.route("/api/get_device_state", methods=["POST"])
def api_get_device_state():
    return jsonify({"error": "legacy direct tool endpoint disabled; use /chat orchestrator"}), 410


@app.route("/api/set_device_state", methods=["POST"])
def api_set_device_state():
    return jsonify({"error": "legacy side-effect endpoint disabled; use /chat orchestrator"}), 410


@app.route("/api/propose_scene", methods=["POST"])
def api_propose_scene():
    return jsonify({"error": "legacy scene write endpoint disabled; use /chat orchestrator"}), 410


@app.route("/api/query_history", methods=["POST"])
def api_query_history():
    return jsonify({"error": "legacy direct tool endpoint disabled; use /chat orchestrator"}), 410


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host=os.environ.get("API_HOST", "127.0.0.1"), port=port)
