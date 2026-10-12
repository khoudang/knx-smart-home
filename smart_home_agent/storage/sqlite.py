from __future__ import annotations

import sqlite3
from pathlib import Path


MIGRATIONS: list[tuple[str, str]] = [
    (
        "0001_initial",
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL UNIQUE,
            canonical_name TEXT NOT NULL,
            aliases TEXT,
            controller TEXT,
            line_code TEXT,
            room TEXT NOT NULL,
            device_type TEXT NOT NULL,
            dali_group TEXT,
            dali_address TEXT,
            control_ga TEXT,
            status_ga TEXT,
            dim_control_ga TEXT,
            dim_status_ga TEXT,
            color_control_ga TEXT,
            color_status_ga TEXT,
            power_circuit TEXT,
            capabilities TEXT NOT NULL DEFAULT '[]',
            supported_values TEXT,
            ha_entity_id TEXT UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1,
            is_sensitive INTEGER NOT NULL DEFAULT 0,
            requires_confirmation INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            source_updated_at TEXT,
            local_updated_at TEXT,
            sync_status TEXT NOT NULL DEFAULT 'active'
        );

        CREATE TABLE IF NOT EXISTS command_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL UNIQUE,
            source_message_id TEXT,
            user_id TEXT,
            channel TEXT,
            raw_input TEXT,
            parsed_intent TEXT,
            resolved_device_ids TEXT,
            resolved_entities TEXT,
            parameters TEXT,
            requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            executed_at TEXT,
            success INTEGER,
            status TEXT,
            error_code TEXT,
            error_message TEXT,
            previous_state TEXT,
            resulting_state TEXT,
            latency_ms INTEGER,
            ai_model TEXT,
            ai_used INTEGER NOT NULL DEFAULT 0,
            confidence REAL,
            sensitive INTEGER NOT NULL DEFAULT 0,
            confirmed INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS scenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scene_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            aliases TEXT,
            scene_type TEXT NOT NULL,
            room TEXT,
            control_ga TEXT,
            ha_entity_id TEXT,
            payload_type TEXT,
            payload_value TEXT,
            trigger_type TEXT,
            trigger_condition TEXT,
            created_by TEXT NOT NULL,
            created_by_user_id TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            requires_confirmation INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            last_executed_at TEXT,
            execution_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS scene_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scene_id TEXT NOT NULL,
            action_order INTEGER NOT NULL,
            device_id TEXT NOT NULL,
            action TEXT NOT NULL,
            value_json TEXT,
            FOREIGN KEY (scene_id) REFERENCES scenes(scene_id),
            FOREIGN KEY (device_id) REFERENCES devices(device_id)
        );

        CREATE TABLE IF NOT EXISTS scene_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id TEXT NOT NULL UNIQUE,
            proposed_name TEXT NOT NULL,
            trigger_type TEXT,
            trigger_condition TEXT,
            proposed_actions TEXT NOT NULL,
            evidence TEXT,
            confidence REAL,
            status TEXT NOT NULL DEFAULT 'pending',
            proposed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            responded_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sync_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_id TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT,
            status TEXT NOT NULL,
            inserted_count INTEGER NOT NULL DEFAULT 0,
            updated_count INTEGER NOT NULL DEFAULT 0,
            unchanged_count INTEGER NOT NULL DEFAULT 0,
            disabled_count INTEGER NOT NULL DEFAULT 0,
            invalid_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT
        );
        """,
    )
]

MIGRATIONS.append(
    (
        "0002_idempotency_and_context",
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_command_log_source_message
            ON command_log(channel, source_message_id);

        CREATE TABLE IF NOT EXISTS confirmations (
            confirmation_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            action_json TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT
        );

        CREATE TABLE IF NOT EXISTS conversation_context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            context_json TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
    )
)

MIGRATIONS.append(
    (
        "0003_users_and_execution_details",
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            display_name TEXT,
            role TEXT NOT NULL DEFAULT 'family',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            UNIQUE(channel, user_id)
        );

        CREATE TABLE IF NOT EXISTS command_executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            device_id TEXT,
            action TEXT NOT NULL,
            parameters TEXT,
            previous_state TEXT,
            expected_state TEXT,
            resulting_state TEXT,
            status TEXT NOT NULL,
            error_code TEXT,
            started_at TEXT,
            completed_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_command_executions_request_id
            ON command_executions(request_id);
        """,
    )
)

MIGRATIONS.append(
    (
        "0004_homebridge_accessory_id",
        """
        ALTER TABLE devices ADD COLUMN homebridge_accessory_id TEXT;
        """,
    )
)

MIGRATIONS.append(
    (
        "0005_access_control",
        """
        ALTER TABLE users ADD COLUMN expires_at TEXT;
        ALTER TABLE users ADD COLUMN allowed_areas TEXT;
        ALTER TABLE users ADD COLUMN allowed_devices TEXT;

        CREATE TABLE IF NOT EXISTS access_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT NOT NULL,
            user_id TEXT NOT NULL,
            display_name TEXT,
            username TEXT,
            requested_role TEXT NOT NULL DEFAULT 'member',
            status TEXT NOT NULL DEFAULT 'pending',
            source_message_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            decided_at TEXT,
            decided_by_channel TEXT,
            decided_by_user_id TEXT,
            expires_at TEXT,
            reason TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_access_requests_one_pending
            ON access_requests(channel, user_id)
            WHERE status = 'pending';

        CREATE INDEX IF NOT EXISTS idx_access_requests_status
            ON access_requests(status, created_at);

        CREATE TABLE IF NOT EXISTS user_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_channel TEXT,
            actor_user_id TEXT,
            target_channel TEXT,
            target_user_id TEXT,
            action TEXT NOT NULL,
            old_role TEXT,
            new_role TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
    )
)

MIGRATIONS.append(
    (
        "0006_user_profile_username",
        """
        ALTER TABLE users ADD COLUMN username TEXT;
        """,
    )
)

MIGRATIONS.append(
    (
        "0007_pending_actions",
        """
        CREATE TABLE IF NOT EXISTS pending_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            intent TEXT NOT NULL,
            resolved_entities TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL,
            completed_at TEXT,
            source_request_id TEXT,
            completed_request_id TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_pending_actions_actor
            ON pending_actions(channel, actor_id, status, expires_at);
        """,
    )
)


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        conn.executescript(sql)
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
    conn.commit()
