from __future__ import annotations

import json
import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from smart_home_agent.services.pipeline_log import log_pipeline_event


CONFIRM_WORDS = {
    "yes",
    "y",
    "ok",
    "okay",
    "dong y",
    "xac nhan",
    "toi dong y",
    "chap nhan",
    "thuc hien",
}
CANCEL_WORDS = {
    "no",
    "n",
    "cancel",
    "huy",
    "khong",
    "bo qua",
    "dung lai",
}


@dataclass(frozen=True)
class PendingAction:
    id: int
    actor_id: str
    channel: str
    intent: str
    resolved_entities: dict[str, Any]
    status: str
    created_at: str
    expires_at: str


def pending_action_ttl_seconds() -> int:
    raw = os.environ.get("PENDING_ACTION_TTL_SECONDS", "120")
    try:
        value = int(raw)
    except ValueError:
        value = 120
    return max(30, min(value, 3600))


def confirmation_decision(text: str) -> str | None:
    normalized = normalize_control_command(text)
    if normalized in CONFIRM_WORDS:
        return "confirm"
    if normalized in CANCEL_WORDS:
        return "cancel"
    return None


def normalize_control_command(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text or "")
    without_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    ascii_text = without_marks.replace("\u0111", "d").replace("\u0110", "D")
    return re.sub(r"\s+", " ", ascii_text.lower()).strip()


def create_pending_action(
    conn: sqlite3.Connection,
    *,
    actor_id: str,
    channel: str,
    intent: str,
    resolved_entities: dict[str, Any],
    source_request_id: str,
    ttl_seconds: int | None = None,
) -> PendingAction:
    expires_at = _utc_after_seconds(ttl_seconds if ttl_seconds is not None else pending_action_ttl_seconds())
    payload = json.dumps(resolved_entities, ensure_ascii=False, sort_keys=True)
    with conn:
        conn.execute(
            """
            UPDATE pending_actions
            SET status = 'cancelled', completed_at = CURRENT_TIMESTAMP
            WHERE channel = ? AND actor_id = ? AND status = 'pending'
            """,
            (channel, actor_id),
        )
        cur = conn.execute(
            """
            INSERT INTO pending_actions (
                actor_id, channel, intent, resolved_entities, status, expires_at, source_request_id
            ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
            """,
            (actor_id, channel, intent, payload, expires_at, source_request_id),
        )
        action_id = int(cur.lastrowid)
    action = get_pending_action(conn, channel=channel, actor_id=actor_id, action_id=action_id)
    assert action is not None
    return action


def get_active_pending_action(
    conn: sqlite3.Connection,
    *,
    channel: str,
    actor_id: str,
) -> PendingAction | None:
    row = conn.execute(
        """
        SELECT *
        FROM pending_actions
        WHERE channel = ? AND actor_id = ? AND status = 'pending'
        ORDER BY id DESC
        LIMIT 1
        """,
        (channel, actor_id),
    ).fetchone()
    if not row:
        return None
    action = _row_to_action(row)
    if is_expired(action):
        mark_pending_action(conn, action.id, status="expired")
        log_pipeline_event(
            event="confirmation_expired",
            pending_action_id=action.id,
            actor_id=actor_id,
            channel=channel,
            intent=action.intent,
        )
        return None
    return action


def get_pending_action(
    conn: sqlite3.Connection,
    *,
    channel: str,
    actor_id: str,
    action_id: int,
) -> PendingAction | None:
    row = conn.execute(
        """
        SELECT *
        FROM pending_actions
        WHERE id = ? AND channel = ? AND actor_id = ?
        """,
        (action_id, channel, actor_id),
    ).fetchone()
    return _row_to_action(row) if row else None


def mark_pending_action(
    conn: sqlite3.Connection,
    action_id: int,
    *,
    status: str,
    completed_request_id: str | None = None,
) -> None:
    if status not in {"completed", "cancelled", "expired"}:
        raise ValueError(f"unsupported pending action status: {status}")
    with conn:
        conn.execute(
            """
            UPDATE pending_actions
            SET status = ?, completed_at = CURRENT_TIMESTAMP, completed_request_id = COALESCE(?, completed_request_id)
            WHERE id = ? AND status = 'pending'
            """,
            (status, completed_request_id, action_id),
        )


def is_expired(action: PendingAction) -> bool:
    expires_at = datetime.fromisoformat(action.expires_at.replace("Z", "+00:00"))
    return datetime.now(timezone.utc) >= expires_at


def _row_to_action(row: sqlite3.Row) -> PendingAction:
    return PendingAction(
        id=int(row["id"]),
        actor_id=row["actor_id"],
        channel=row["channel"],
        intent=row["intent"],
        resolved_entities=json.loads(row["resolved_entities"] or "{}"),
        status=row["status"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )


def _utc_after_seconds(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
