from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass

from smart_home_agent.integrations.device_control import ControlCommand, DeviceControlClient
from smart_home_agent.services.command_log import find_by_source_message, record_execution


@dataclass(frozen=True)
class SceneExecutionResult:
    status: str
    executed_actions: int
    message: str


def create_scene_proposal(
    conn: sqlite3.Connection,
    proposed_name: str,
    proposed_actions: list[dict],
    evidence: str | None = None,
    confidence: float | None = None,
) -> str:
    proposal_id = f"proposal_{uuid.uuid4().hex[:12]}"
    with conn:
        conn.execute(
            """
            INSERT INTO scene_proposals (
                proposal_id, proposed_name, proposed_actions, evidence, confidence
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                proposal_id,
                proposed_name,
                json.dumps(proposed_actions, ensure_ascii=False),
                evidence,
                confidence,
            ),
        )
    return proposal_id


def accept_scene_proposal(
    conn: sqlite3.Connection,
    proposal_id: str,
    created_by: str,
    created_by_user_id: str,
) -> str:
    row = conn.execute(
        "SELECT * FROM scene_proposals WHERE proposal_id = ? AND status = 'pending'",
        (proposal_id,),
    ).fetchone()
    if not row:
        raise ValueError("pending scene proposal not found")

    actions = json.loads(row["proposed_actions"])
    scene_id = f"scene_{uuid.uuid4().hex[:12]}"
    with conn:
        conn.execute(
            """
            INSERT INTO scenes (
                scene_id, name, scene_type, created_by, created_by_user_id
            ) VALUES (?, ?, 'local_scene', ?, ?)
            """,
            (scene_id, row["proposed_name"], created_by, created_by_user_id),
        )
        for idx, action in enumerate(actions, start=1):
            conn.execute(
                """
                INSERT INTO scene_actions (scene_id, action_order, device_id, action, value_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    scene_id,
                    idx,
                    action["device_id"],
                    action["action"],
                    json.dumps(action.get("value", {}), ensure_ascii=False),
                ),
            )
        conn.execute(
            "UPDATE scene_proposals SET status = 'accepted', responded_at = CURRENT_TIMESTAMP WHERE proposal_id = ?",
            (proposal_id,),
        )
    return scene_id


def execute_scene_once(
    conn: sqlite3.Connection,
    scene_id: str,
    request_id: str,
    source_message_id: str,
    channel: str,
    device_client: DeviceControlClient,
) -> SceneExecutionResult:
    if find_by_source_message(conn, channel, source_message_id):
        return SceneExecutionResult("duplicate", 0, "scene request already processed")

    scene = conn.execute(
        "SELECT * FROM scenes WHERE scene_id = ? AND enabled = 1",
        (scene_id,),
    ).fetchone()
    if not scene:
        return SceneExecutionResult("validation_failed", 0, "scene not found")

    actions = conn.execute(
        """
        SELECT sa.action, sa.value_json, d.device_id, d.device_type,
               d.control_ga, d.status_ga, d.dim_control_ga, d.dim_status_ga,
               d.color_control_ga, d.color_status_ga
        FROM scene_actions sa
        JOIN devices d ON d.device_id = sa.device_id
        WHERE sa.scene_id = ?
        ORDER BY sa.action_order ASC
        """,
        (scene_id,),
    ).fetchall()

    executed = 0
    with conn:
        conn.execute(
            """
            INSERT INTO command_log (
                request_id, source_message_id, channel, raw_input, status
            ) VALUES (?, ?, ?, ?, 'scene_running')
            """,
            (request_id, source_message_id, channel, f"scene:{scene_id}"),
        )
        for action in actions:
            value = json.loads(action["value_json"] or "{}")
            expected = {"state": "on"} if action["action"] == "turn_on" else {"state": "off"}
            if action["action"] not in {"turn_on", "turn_off"}:
                raise ValueError(f"unsupported scene action {action['action']}")
            result = device_client.execute(
                ControlCommand(
                    device_id=action["device_id"],
                    device_type=action["device_type"],
                    action=action["action"],
                    control_ga=action["control_ga"],
                    status_ga=action["status_ga"],
                    dim_control_ga=action["dim_control_ga"],
                    dim_status_ga=action["dim_status_ga"],
                    color_control_ga=action["color_control_ga"],
                    color_status_ga=action["color_status_ga"],
                    value=value,
                    expected_state=expected,
                )
            )
            record_execution(
                conn,
                request_id=request_id,
                action=action["action"],
                status=result.status,
                parameters=value,
                expected_state=expected,
                resulting_state=result.state or result.message,
                error_code=None if result.status in {"success", "accepted_but_unverified"} else result.status,
            )
            if result.status not in {"success", "accepted_but_unverified"}:
                raise ValueError(result.message or result.status)
            executed += 1
        conn.execute(
            """
            UPDATE command_log
            SET status = 'success', success = 1, executed_at = CURRENT_TIMESTAMP,
                resulting_state = ?
            WHERE request_id = ?
            """,
            (f"executed {executed} scene actions", request_id),
        )
        conn.execute(
            """
            UPDATE scenes
            SET execution_count = execution_count + 1, last_executed_at = CURRENT_TIMESTAMP
            WHERE scene_id = ?
            """,
            (scene_id,),
        )
    return SceneExecutionResult("success", executed, "scene executed")
