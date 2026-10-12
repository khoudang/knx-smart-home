from __future__ import annotations

import json
import sqlite3

from smart_home_agent.agent_core.command_models import AdapterInput, Intent


def begin_command(conn: sqlite3.Connection, adapter_input: AdapterInput) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO command_log (
                request_id, source_message_id, user_id, channel, raw_input, status
            ) VALUES (?, ?, ?, ?, ?, 'received')
            """,
            (
                adapter_input.request_id,
                adapter_input.source_message_id,
                adapter_input.user_id,
                adapter_input.channel,
                adapter_input.text,
            ),
        )


def find_by_source_message(
    conn: sqlite3.Connection, channel: str, source_message_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM command_log
        WHERE channel = ? AND source_message_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (channel, source_message_id),
    ).fetchone()


def complete_command(
    conn: sqlite3.Connection,
    request_id: str,
    *,
    status: str,
    success: bool,
    parsed_intent: Intent | None = None,
    resolved_device_ids: list[str] | None = None,
    resolved_entities: list[str] | None = None,
    resulting_state: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    sensitive: bool = False,
    confirmed: bool = False,
    latency_ms: int | None = None,
) -> None:
    with conn:
        conn.execute(
            """
            UPDATE command_log
            SET parsed_intent = ?, resolved_device_ids = ?, resolved_entities = ?,
                executed_at = CURRENT_TIMESTAMP, success = ?, status = ?,
                error_code = ?, error_message = ?, resulting_state = ?,
                latency_ms = ?, confidence = ?, sensitive = ?, confirmed = ?
            WHERE request_id = ?
            """,
            (
                json.dumps(parsed_intent.__dict__, ensure_ascii=False) if parsed_intent else None,
                json.dumps(resolved_device_ids or [], ensure_ascii=False),
                json.dumps(resolved_entities or [], ensure_ascii=False),
                int(success),
                status,
                error_code,
                error_message,
                resulting_state,
                latency_ms,
                parsed_intent.confidence if parsed_intent else None,
                int(sensitive),
                int(confirmed),
                request_id,
            ),
        )


def record_execution(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    action: str,
    status: str,
    device_id: str | None = None,
    parameters: dict | None = None,
    expected_state: dict | None = None,
    resulting_state: dict | str | None = None,
    error_code: str | None = None,
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO command_executions (
                request_id, device_id, action, parameters, expected_state,
                resulting_state, status, error_code, started_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                request_id,
                device_id,
                action,
                json.dumps(parameters or {}, ensure_ascii=False),
                json.dumps(expected_state or {}, ensure_ascii=False),
                json.dumps(resulting_state, ensure_ascii=False) if not isinstance(resulting_state, str) else resulting_state,
                status,
                error_code,
            ),
        )
