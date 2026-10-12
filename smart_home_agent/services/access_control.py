from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from smart_home_agent.services.user_repository import VALID_ROLES, normalize_role, resolve_user_role, upsert_user


REQUEST_PHRASES = {
    "xin quyen",
    "xin quy?n",
    "xin cap quyen",
    "xin vao bot",
    "cho toi dung bot",
    "cho minh dung bot",
    "cho em dung bot",
    "cho anh dung bot",
    "cho chi dung bot",
    "mo quyen",
    "dang ky",
    "d?ng ky",
    "request access",
}
REQUEST_STATUSES = {"pending", "approved", "rejected", "cancelled", "expired"}
APPROVABLE_ROLES = {"admin", "member", "viewer", "guest"}
READ_ONLY_INTENTS = {"greeting", "count_devices", "list_devices", "status_summary", "device_info", "read_state"}
ADMIN_WRITE_ACTIONS = {
    "approve",
    "approve_current",
    "reject",
    "reject_current",
    "update_role",
    "disable",
    "enable",
    "enable_by_query",
    "revoke",
    "revoke_by_query",
    "revoke_all_permissions",
    "block_user",
    "remove_user",
}


@dataclass(frozen=True)
class AccessRequestResult:
    request_id: int | None
    status: str
    created: bool
    reply: str
    notifications: list[dict]


@dataclass(frozen=True)
class AdminCommand:
    action: str
    request_id: int | None = None
    role: str | None = None
    duration_days: int | None = None
    channel: str | None = None
    user_id: str | None = None
    target_query: str | None = None


@dataclass(frozen=True)
class AdminCommandResult:
    reply: str
    notifications: list[dict] | None = None


@dataclass(frozen=True)
class AdminIntentSpec:
    action: str
    exact: frozenset[str] = frozenset()
    contains_all: tuple[tuple[str, ...], ...] = ()


ADMIN_INTENT_SPECS = (
    AdminIntentSpec(
        "list_requests",
        exact=frozenset({"danh sach yeu cau", "list requests", "yeu cau cho duyet"}),
        contains_all=(("yeu cau", "cho duyet"), ("ai", "xin quyen")),
    ),
    AdminIntentSpec(
        "count_members",
        exact=frozenset({
            "co may user",
            "co bao nhieu user",
            "co may thanh vien",
            "co bao nhieu thanh vien",
            "co may user trong he thong",
            "co bao nhieu user trong he thong",
            "co may nguoi dung trong he thong",
            "co bao nhieu nguoi dung trong he thong",
            "co may nguoi trong he thong",
            "co bao nhieu nguoi trong he thong",
            "co may tai khoan",
            "co bao nhieu tai khoan",
            "co may tai khoan trong he thong",
            "co bao nhieu tai khoan trong he thong",
        }),
        contains_all=(("co may", "quyen"), ("bao nhieu", "quyen"), ("bao nhieu", "duoc dung bot")),
    ),
    AdminIntentSpec(
        "list_members",
        exact=frozenset({
            "danh sach thanh vien",
            "list members",
            "danh sach user",
            "danh sach nguoi dung",
            "danh sach nguoi",
            "danh sach tai khoan",
        }),
        contains_all=(
            ("ai", "co quyen"),
            ("ai", "duoc dung bot"),
            ("ai", "dung duoc bot"),
            ("nhung ai", "co quyen"),
            ("nhung ai", "duoc dung bot"),
            ("toi da cap quyen cho ai",),
            ("da cap quyen cho ai",),
            ("cap quyen cho ai",),
            ("quyen", "cho ai"),
            ("danh sach", "quyen"),
            ("danh sach", "thanh vien"),
            ("danh sach", "nguoi dung"),
        ),
    ),
    AdminIntentSpec(
        "list_blocked",
        exact=frozenset({
            "he thong co chan ai khong",
            "co chan ai khong",
            "dang chan ai",
            "ai dang bi chan",
            "danh sach bi chan",
            "danh sach nguoi bi chan",
            "danh sach user bi chan",
        }),
        contains_all=(("chan", "ai"), ("ai", "bi chan"), ("danh sach", "bi chan")),
    ),
    AdminIntentSpec(
        "enable_missing_target",
        exact=frozenset({"mo chan", "bo chan", "go chan", "mo lai quyen"}),
    ),
    AdminIntentSpec(
        "revoke_missing_target",
        exact=frozenset({"thu hoi quyen", "revoke", "xoa quyen", "tuoc quyen", "go quyen", "chan khoi bot"}),
    ),
    AdminIntentSpec(
        "reject_current",
        exact=frozenset({"khong", "kh?ng", "ko", "k", "no", "cancel", "huy", "tu choi", "khong dong y", "khong cho phep", "reject"}),
    ),
    AdminIntentSpec(
        "approve_current",
        exact=frozenset({"co", "c?", "dong y", "cho phep", "ok", "okay", "duyet"}),
        contains_all=(("cho", "lam member"),),
    ),
)


ADMIN_DOMAIN_TERMS = {
    "quyen",
    "cap quyen",
    "thu hoi quyen",
    "xoa quyen",
    "tuoc quyen",
    "go quyen",
    "xin quyen",
    "duyet",
    "tu choi",
    "thanh vien",
    "nguoi dung",
    "user",
    "tai khoan",
    "chu nha",
    "dung bot",
    "duoc dung bot",
    "lam member",
    "bi chan",
    "mo chan",
    "go chan",
}


def normalized_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text or "")
    ascii_text = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    ascii_text = ascii_text.replace("\u0111", "d").replace("\u0110", "D")
    return re.sub(r"\s+", " ", ascii_text.lower()).strip().strip(" ?!.")


def is_access_request_text(text: str) -> bool:
    normalized = normalized_text(text)
    return normalized in REQUEST_PHRASES


def is_admin_domain_text(text: str) -> bool:
    normalized = normalized_text(text)
    return any(term in normalized for term in ADMIN_DOMAIN_TERMS)


def mask_user_id(user_id: str) -> str:
    text = str(user_id or "")
    if len(text) <= 6:
        return "***"
    return f"{text[:3]}***{text[-3:]}"


def is_admin_write_action(action: str | None) -> bool:
    return (action or "") in ADMIN_WRITE_ACTIONS


def create_access_request(
    conn: sqlite3.Connection,
    *,
    channel: str,
    user_id: str,
    display_name: str | None = None,
    username: str | None = None,
    requested_role: str = "member",
    source_message_id: str | None = None,
) -> AccessRequestResult:
    user = resolve_user_role(conn, channel, user_id)
    if user.known and (not user.enabled or user.role == "blocked"):
        return AccessRequestResult(
            None,
            "blocked",
            False,
            "Tài khoản này đã bị chặn.",
            [],
        )
    if user.known:
        return AccessRequestResult(
            None,
            "already_authorized",
            False,
            "Tài khoản của bạn đã có quyền sử dụng bot.",
            [],
        )

    requested_role = normalize_role(requested_role)
    if requested_role not in APPROVABLE_ROLES:
        requested_role = "member"

    with conn:
        existing = _pending_request(conn, channel, user_id)
        if existing:
            _update_request_profile(
                conn,
                int(existing["id"]),
                display_name=display_name,
                username=username,
            )
            return AccessRequestResult(
                existing["id"],
                "pending",
                False,
                "Yêu cầu xin quyền của bạn đã được gửi tới chủ nhà. Vui lòng chờ phê duyệt.",
                owner_notifications(conn, int(existing["id"])),
            )
        cur = conn.execute(
            """
            INSERT INTO access_requests (
                channel, user_id, display_name, username, requested_role, source_message_id, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (channel, user_id, display_name, username, requested_role, source_message_id),
        )
        request_id = int(cur.lastrowid)
        _audit(
            conn,
            actor_channel=channel,
            actor_user_id=user_id,
            target_channel=channel,
            target_user_id=user_id,
            action="access_request_created",
            metadata={"request_id": request_id, "requested_role": requested_role},
        )

    return AccessRequestResult(
        request_id,
        "pending",
        True,
        "Yêu cầu xin quyền của bạn đã được gửi tới chủ nhà. Vui lòng chờ phê duyệt.",
        owner_notifications(conn, request_id),
    )


def owner_notifications(conn: sqlite3.Connection, request_id: int) -> list[dict]:
    request = conn.execute("SELECT * FROM access_requests WHERE id = ?", (request_id,)).fetchone()
    if not request:
        return []
    owners = conn.execute(
        """
        SELECT channel, user_id
        FROM users
        WHERE enabled = 1 AND role = 'owner'
        ORDER BY channel, user_id
        """
    ).fetchall()
    requested_at = request["created_at"] or ""
    display_name = request["display_name"] or "(không có tên)"
    username = request["username"] or ""
    channel_label = str(request["channel"] or "").capitalize()
    username_line = f"Username: @{username}\n" if username else ""
    text = (
        "Có yêu cầu xin quyền mới.\n"
        f"Người dùng: {display_name}\n"
        f"{username_line}"
        f"Kênh: {channel_label}\n"
        f"User ID: {mask_user_id(request['user_id'])}\n"
        f"Thời gian: {requested_at}\n"
        f"Mã yêu cầu: {request_id}\n"
        f"Duyệt: duyệt {request_id} member | viewer | guest 7d\n"
        f"Từ chối: từ chối {request_id}"
    )
    return [{"channel": row["channel"], "user_id": row["user_id"], "text": text} for row in owners]


def owner_notifications(conn: sqlite3.Connection, request_id: int) -> list[dict]:
    request = conn.execute("SELECT * FROM access_requests WHERE id = ?", (request_id,)).fetchone()
    if not request:
        return []
    owners = conn.execute(
        """
        SELECT channel, user_id
        FROM users
        WHERE enabled = 1 AND role = 'owner'
        ORDER BY channel, user_id
        """
    ).fetchall()
    display_name = request["display_name"] or "Không rõ tên"
    username = request["username"] or ""
    channel_label = str(request["channel"] or "").capitalize()
    username_line = f"Username: @{username}\n" if username else ""
    text = (
        "Có yêu cầu xin quyền mới.\n"
        f"Người dùng: {display_name}\n"
        f"{username_line}"
        f"Kênh: {channel_label}\n"
        f"Mã yêu cầu: {request_id}\n"
        "Trả lời: Đồng ý để cấp quyền Thành viên, hoặc Từ chối."
    )
    return [{"channel": row["channel"], "user_id": row["user_id"], "text": text} for row in owners]


def parse_admin_command(text: str) -> AdminCommand | None:
    normalized = normalized_text(text)
    spec_command = _command_from_admin_specs(normalized)
    if spec_command:
        return spec_command

    match = re.match(r"^(duyet|approve)\s+(\d+)\s+([a-z]+)(?:\s+(\d+)d)?$", normalized)
    if match:
        role = normalize_role(match.group(3))
        days = int(match.group(4)) if match.group(4) else None
        return AdminCommand("approve", request_id=int(match.group(2)), role=role, duration_days=days)

    match = re.match(r"^(tu choi|reject)\s+(\d+)(?:\s+(.+))?$", normalized)
    if match:
        return AdminCommand("reject", request_id=int(match.group(2)))

    match = re.match(r"^(doi quyen|update-role)\s+(\w+)\s+(\S+)\s+([a-z]+)$", normalized)
    if match:
        return AdminCommand(
            "update_role",
            channel=match.group(2),
            user_id=match.group(3),
            role=normalize_role(match.group(4)),
        )

    match = re.match(r"^(vo hieu hoa|disable)\s+(\w+)\s+(\S+)$", normalized)
    if match:
        return AdminCommand("disable", channel=match.group(2), user_id=match.group(3))

    match = re.match(r"^(mo lai|enable)\s+(\w+)\s+(\S+)$", normalized)
    if match:
        return AdminCommand("enable", channel=match.group(2), user_id=match.group(3))

    match = re.match(r"^(mo chan|bo chan|go chan|mo lai quyen|enable user)\s+(?:cho\s+)?(.+?)(?:\s+(?:di|nhe|giup toi|dum toi))?$", normalized)
    if match:
        target = match.group(2).strip()
        if not target:
            return AdminCommand("enable_missing_target")
        return AdminCommand("enable_by_query", target_query=target)

    match = re.match(r"^(xoa|loai|go)\s+(.+?)\s+ra\s+khoi\s+(?:nha|bot|he thong)(?:\s+(?:di|nhe|giup toi|dum toi))?$", normalized)
    if match:
        target = match.group(2).strip()
        if not target:
            return AdminCommand("revoke_missing_target")
        return AdminCommand("revoke_by_query", target_query=target)

    match = re.match(r"^(thu hoi quyen|revoke|xoa quyen|tuoc quyen|go quyen|chan khoi bot|chan)\s+(telegram|zalo|api)\s+(\S+)(?:\s+(?:di|nhe))?$", normalized)
    if match:
        return AdminCommand("revoke", channel=match.group(2), user_id=match.group(3))

    match = re.match(r"^(thu hoi quyen|revoke|xoa quyen|tuoc quyen|go quyen|chan khoi bot|chan)\s+(.+)$", normalized)
    if match:
        target = re.sub(r"\s+(?:di|nhe|giup toi|dum toi|cho toi)$", "", match.group(2).strip()).strip()
        if not target:
            return AdminCommand("revoke_missing_target")
        return AdminCommand("revoke_by_query", target_query=target)

    return None


def _command_from_admin_specs(normalized: str) -> AdminCommand | None:
    for spec in ADMIN_INTENT_SPECS:
        if normalized in spec.exact or any(all(part in normalized for part in group) for group in spec.contains_all):
            if spec.action == "approve_current":
                return AdminCommand(spec.action, role="member")
            return AdminCommand(spec.action)
    return None


def execute_admin_command(conn: sqlite3.Connection, actor_channel: str, actor_user_id: str, command: AdminCommand) -> AdminCommandResult:
    actor = resolve_user_role(conn, actor_channel, actor_user_id)
    if not actor.known or not actor.enabled or actor.role not in {"owner", "admin"}:
        return AdminCommandResult("Bạn không có quyền quản trị.")

    if command.action == "list_requests":
        return AdminCommandResult(_format_requests(conn))
    if command.action == "list_members":
        return AdminCommandResult(_format_members(conn))
    if command.action == "list_blocked":
        return AdminCommandResult(_format_blocked_users(conn))
    if command.action == "count_members":
        return AdminCommandResult(_format_member_count(conn))
    if command.action == "revoke_missing_target":
        return AdminCommandResult("Bạn muốn thu hồi quyền của ai? Hãy gửi: thu hồi quyền <tên, username hoặc user ID>.")
    if command.action == "enable_missing_target":
        return AdminCommandResult("Bạn muốn mở chặn cho ai? Hãy gửi: mở chặn cho <tên, username hoặc user ID>.")
    if command.action == "request_decision_help":
        pending = _single_pending_request(conn)
        if pending:
            return AdminCommandResult("Bạn có thể trả lời: Đồng ý để cấp quyền Thành viên, hoặc Từ chối.")
        if _pending_request_count(conn) > 1:
            return AdminCommandResult("Đang có nhiều yêu cầu chờ duyệt. Hãy mở danh sách yêu cầu rồi chọn mã cần xử lý.")
        return AdminCommandResult("Hiện không có yêu cầu xin quyền nào đang chờ duyệt.")
    if command.action == "approve_current":
        pending = _single_pending_request(conn)
        if not pending:
            if _pending_request_count(conn) > 1:
                return AdminCommandResult("Đang có nhiều yêu cầu chờ duyệt. Hãy dùng: duyệt <mã yêu cầu> member.")
            return AdminCommandResult("Bạn đang đồng ý với yêu cầu nào?")
        command = AdminCommand("approve", request_id=int(pending["id"]), role=command.role or "member")
    if command.action == "reject_current":
        pending = _single_pending_request(conn)
        if not pending:
            if _pending_request_count(conn) > 1:
                return AdminCommandResult("Đang có nhiều yêu cầu chờ duyệt. Hãy dùng: từ chối <mã yêu cầu>.")
            return AdminCommandResult("Bạn đang muốn từ chối yêu cầu nào?")
        command = AdminCommand("reject", request_id=int(pending["id"]))
    if command.action == "request_decision_help":
        pending = conn.execute(
            "SELECT id FROM access_requests WHERE status = 'pending' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if pending:
            return AdminCommandResult(
                f"Nếu muốn từ chối yêu cầu, hãy gửi: từ chối {pending['id']}. "
                f"Nếu muốn duyệt, gửi: duyệt {pending['id']} member."
            )
        return AdminCommandResult("Hiện không có yêu cầu xin quyền nào đang chờ duyệt.")
    if command.action == "approve":
        request = _request_by_id(conn, _required_request_id(command))
        reply = approve_request(
            conn,
            request_id=_required_request_id(command),
            actor_channel=actor_channel,
            actor_user_id=actor_user_id,
            role=command.role or "member",
            guest_days=command.duration_days,
        )
        notifications = []
        if request and request["status"] == "pending" and reply.startswith("Đã duyệt"):
            notifications.append(
                {
                    "channel": request["channel"],
                    "user_id": request["user_id"],
                    "text": "Yêu cầu xin quyền của bạn đã được duyệt. Bạn có thể sử dụng bot từ bây giờ.",
                }
            )
        return AdminCommandResult(reply, notifications)
    if command.action == "reject":
        request = _request_by_id(conn, _required_request_id(command))
        reply = reject_request(
            conn,
            request_id=_required_request_id(command),
            actor_channel=actor_channel,
            actor_user_id=actor_user_id,
        )
        notifications = []
        if request and request["status"] == "pending" and reply.startswith("Đã từ chối"):
            notifications.append(
                {
                    "channel": request["channel"],
                    "user_id": request["user_id"],
                    "text": "Yêu cầu xin quyền của bạn đã bị từ chối.",
                }
            )
        return AdminCommandResult(reply, notifications)
    if command.action == "update_role":
        _require_owner_for_owner_change(conn, actor, command.channel, command.user_id, command.role)
        return AdminCommandResult(update_role(
            conn,
            actor_channel=actor_channel,
            actor_user_id=actor_user_id,
            channel=_required(command.channel, "channel"),
            user_id=_required(command.user_id, "user_id"),
            role=_required(command.role, "role"),
        ))
    if command.action == "disable":
        return AdminCommandResult(set_user_enabled(
            conn,
            actor_channel=actor_channel,
            actor_user_id=actor_user_id,
            channel=_required(command.channel, "channel"),
            user_id=_required(command.user_id, "user_id"),
            enabled=False,
        ))
    if command.action == "enable":
        return AdminCommandResult(set_user_enabled(
            conn,
            actor_channel=actor_channel,
            actor_user_id=actor_user_id,
            channel=_required(command.channel, "channel"),
            user_id=_required(command.user_id, "user_id"),
            enabled=True,
        ))
    if command.action == "enable_by_query":
        matches = _find_users(conn, _required(command.target_query, "target_query"))
        if not matches:
            return AdminCommandResult("Không tìm thấy người dùng phù hợp. Hãy thử bằng tên, username hoặc user ID đầy đủ.")
        blocked = [row for row in matches if row["role"] == "blocked" or not row["enabled"]]
        candidates = blocked or matches
        if len(candidates) > 1:
            options = "; ".join(
                f"{row['display_name'] or row['username'] or row['channel']} ({row['channel']} {mask_user_id(row['user_id'])}, role={row['role']})"
                for row in candidates[:5]
            )
            return AdminCommandResult(f"Tìm thấy nhiều người dùng phù hợp: {options}. Hãy gửi rõ hơn bằng username hoặc user ID.")
        row = candidates[0]
        return AdminCommandResult(unblock_user(
            conn,
            actor_channel=actor_channel,
            actor_user_id=actor_user_id,
            channel=row["channel"],
            user_id=row["user_id"],
        ))
    if command.action == "revoke":
        return AdminCommandResult(revoke_user(
            conn,
            actor_channel=actor_channel,
            actor_user_id=actor_user_id,
            channel=_required(command.channel, "channel"),
            user_id=_required(command.user_id, "user_id"),
        ))
    if command.action == "revoke_by_query":
        matches = _find_users(conn, _required(command.target_query, "target_query"))
        if not matches:
            return AdminCommandResult("Không tìm thấy người dùng phù hợp. Hãy thử bằng tên, username hoặc user ID đầy đủ.")
        if len(matches) > 1:
            options = "; ".join(
                f"{row['display_name'] or row['username'] or row['channel']} ({row['channel']} {mask_user_id(row['user_id'])}, role={row['role']})"
                for row in matches[:5]
            )
            return AdminCommandResult(f"Tìm thấy nhiều người dùng phù hợp: {options}. Hãy gửi rõ hơn bằng username hoặc user ID.")
        row = matches[0]
        return AdminCommandResult(revoke_user(
            conn,
            actor_channel=actor_channel,
            actor_user_id=actor_user_id,
            channel=row["channel"],
            user_id=row["user_id"],
        ))
    return AdminCommandResult("Lệnh quản trị không được hỗ trợ.")


def approve_request(
    conn: sqlite3.Connection,
    *,
    request_id: int,
    actor_channel: str,
    actor_user_id: str,
    role: str,
    guest_days: int | None = None,
) -> str:
    role = normalize_role(role)
    if role not in APPROVABLE_ROLES:
        raise ValueError(f"unsupported approvable role: {role}")
    expires_at = None
    if role == "guest":
        days = guest_days or 7
        expires_at = _utc_after_days(days)

    with conn:
        row = _pending_request_by_id(conn, request_id)
        if not row:
            existing = _request_by_id(conn, request_id)
            if existing:
                return f"Yêu cầu #{request_id} đã được xử lý với trạng thái {existing['status']}."
            return f"Không tìm thấy yêu cầu pending #{request_id}."
        upsert_user(
            conn,
            channel=row["channel"],
            user_id=row["user_id"],
            display_name=row["display_name"],
            role=role,
            enabled=True,
            expires_at=expires_at,
            username=row["username"],
        )
        conn.execute(
            """
            UPDATE access_requests
            SET status = 'approved', decided_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
                decided_by_channel = ?, decided_by_user_id = ?, expires_at = ?
            WHERE id = ?
            """,
            (actor_channel, actor_user_id, expires_at, request_id),
        )
        _audit(
            conn,
            actor_channel=actor_channel,
            actor_user_id=actor_user_id,
            target_channel=row["channel"],
            target_user_id=row["user_id"],
            action="access_request_approved",
            new_role=role,
            metadata={"request_id": request_id, "expires_at": expires_at},
        )
    return f"Đã duyệt yêu cầu #{request_id} với role {role}."


def reject_request(
    conn: sqlite3.Connection,
    *,
    request_id: int,
    actor_channel: str,
    actor_user_id: str,
    reason: str | None = None,
) -> str:
    with conn:
        row = _pending_request_by_id(conn, request_id)
        if not row:
            existing = _request_by_id(conn, request_id)
            if existing:
                return f"Yêu cầu #{request_id} đã được xử lý với trạng thái {existing['status']}."
            return f"Không tìm thấy yêu cầu pending #{request_id}."
        conn.execute(
            """
            UPDATE access_requests
            SET status = 'rejected', reason = ?, decided_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
                decided_by_channel = ?, decided_by_user_id = ?
            WHERE id = ?
            """,
            (reason, actor_channel, actor_user_id, request_id),
        )
        _audit(
            conn,
            actor_channel=actor_channel,
            actor_user_id=actor_user_id,
            target_channel=row["channel"],
            target_user_id=row["user_id"],
            action="access_request_rejected",
            metadata={"request_id": request_id},
        )
    return f"Đã từ chối yêu cầu #{request_id}."


def update_role(
    conn: sqlite3.Connection,
    *,
    actor_channel: str,
    actor_user_id: str,
    channel: str,
    user_id: str,
    role: str,
) -> str:
    role = normalize_role(role)
    if role not in VALID_ROLES:
        raise ValueError(f"unsupported role: {role}")
    current = resolve_user_role(conn, channel, user_id)
    if not current.known:
        return "Không tìm thấy người dùng."
    if current.role == "owner" and role != "owner" and _is_last_enabled_owner(conn, channel, user_id):
        return "Không thể hạ quyền owner cuối cùng."
    with conn:
        upsert_user(conn, channel=channel, user_id=user_id, role=role, enabled=current.enabled, expires_at=current.expires_at)
        _audit(
            conn,
            actor_channel=actor_channel,
            actor_user_id=actor_user_id,
            target_channel=channel,
            target_user_id=user_id,
            action="user_role_updated",
            old_role=current.role,
            new_role=role,
        )
    return f"Đã cập nhật {channel}:{mask_user_id(user_id)} thành {role}."


def set_user_enabled(
    conn: sqlite3.Connection,
    *,
    actor_channel: str,
    actor_user_id: str,
    channel: str,
    user_id: str,
    enabled: bool,
) -> str:
    current = resolve_user_role(conn, channel, user_id)
    if not current.known:
        return "Không tìm thấy người dùng."
    if not enabled and current.role == "owner" and _is_last_enabled_owner(conn, channel, user_id):
        return "Không thể vô hiệu hóa owner cuối cùng."
    with conn:
        upsert_user(conn, channel=channel, user_id=user_id, role=current.role, enabled=enabled, expires_at=current.expires_at)
        _audit(
            conn,
            actor_channel=actor_channel,
            actor_user_id=actor_user_id,
            target_channel=channel,
            target_user_id=user_id,
            action="user_enabled" if enabled else "user_disabled",
            old_role=current.role,
            new_role=current.role,
        )
    return ("Đã mở lại " if enabled else "Đã vô hiệu hóa ") + f"{channel}:{mask_user_id(user_id)}."


def revoke_user(
    conn: sqlite3.Connection,
    *,
    actor_channel: str,
    actor_user_id: str,
    channel: str,
    user_id: str,
) -> str:
    current = resolve_user_role(conn, channel, user_id)
    if not current.known:
        return "Không tìm thấy người dùng."
    if current.role == "owner" and _is_last_enabled_owner(conn, channel, user_id):
        return "Không thể thu hồi owner cuối cùng."
    with conn:
        upsert_user(conn, channel=channel, user_id=user_id, role="blocked", enabled=False)
        _audit(
            conn,
            actor_channel=actor_channel,
            actor_user_id=actor_user_id,
            target_channel=channel,
            target_user_id=user_id,
            action="user_revoked",
            old_role=current.role,
            new_role="blocked",
        )
    return f"Đã thu hồi quyền {channel}:{mask_user_id(user_id)}."


def unblock_user(
    conn: sqlite3.Connection,
    *,
    actor_channel: str,
    actor_user_id: str,
    channel: str,
    user_id: str,
) -> str:
    current = resolve_user_role(conn, channel, user_id)
    if not current.known:
        return "Khong tim thay nguoi dung."
    restored_role = _last_role_before_block(conn, channel, user_id) or "member"
    if restored_role == "blocked":
        restored_role = "member"
    with conn:
        upsert_user(conn, channel=channel, user_id=user_id, role=restored_role, enabled=True, expires_at=None)
        _audit(
            conn,
            actor_channel=actor_channel,
            actor_user_id=actor_user_id,
            target_channel=channel,
            target_user_id=user_id,
            action="user_unblocked",
            old_role=current.role,
            new_role=restored_role,
        )
    return f"Đã mở chặn {channel}:{mask_user_id(user_id)} thành {restored_role}."


def expire_guests(conn: sqlite3.Connection) -> int:
    with conn:
        rows = conn.execute(
            """
            SELECT channel, user_id
            FROM users
            WHERE role = 'guest' AND enabled = 1 AND expires_at IS NOT NULL AND expires_at <= CURRENT_TIMESTAMP
            """
        ).fetchall()
        for row in rows:
            upsert_user(conn, channel=row["channel"], user_id=row["user_id"], role="guest", enabled=False)
            _audit(
                conn,
                actor_channel="system",
                actor_user_id="system",
                target_channel=row["channel"],
                target_user_id=row["user_id"],
                action="guest_expired",
            )
    return len(rows)


def _pending_request(conn: sqlite3.Connection, channel: str, user_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM access_requests
        WHERE channel = ? AND user_id = ? AND status = 'pending'
        ORDER BY id DESC
        LIMIT 1
        """,
        (channel, user_id),
    ).fetchone()


def _pending_request_by_id(conn: sqlite3.Connection, request_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM access_requests WHERE id = ? AND status = 'pending'",
        (request_id,),
    ).fetchone()


def _update_request_profile(
    conn: sqlite3.Connection,
    request_id: int,
    *,
    display_name: str | None = None,
    username: str | None = None,
) -> None:
    if not (display_name or username):
        return
    conn.execute(
        """
        UPDATE access_requests
        SET display_name = COALESCE(NULLIF(?, ''), display_name),
            username = COALESCE(NULLIF(?, ''), username),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (display_name, username, request_id),
    )


def _request_by_id(conn: sqlite3.Connection, request_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM access_requests WHERE id = ?",
        (request_id,),
    ).fetchone()


def _pending_request_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM access_requests WHERE status = 'pending'").fetchone()[0])


def _single_pending_request(conn: sqlite3.Connection) -> sqlite3.Row | None:
    rows = conn.execute(
        "SELECT * FROM access_requests WHERE status = 'pending' ORDER BY id DESC LIMIT 2"
    ).fetchall()
    if len(rows) == 1:
        return rows[0]
    return None


def _format_requests(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """
        SELECT id, channel, user_id, display_name, requested_role, created_at
        FROM access_requests
        WHERE status = 'pending'
        ORDER BY id
        """
    ).fetchall()
    if not rows:
        return "Không có yêu cầu pending."
    lines = ["Yêu cầu pending:"]
    for row in rows:
        name = row["display_name"] or ""
        lines.append(
            f"#{row['id']} {row['channel']} {mask_user_id(row['user_id'])} {name} role={row['requested_role']} at={row['created_at']}"
        )
    return "\n".join(lines)


def _format_members(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """
        SELECT channel, user_id, display_name, username, role, enabled, expires_at
        FROM users
        ORDER BY role, channel, user_id
        """
    ).fetchall()
    if not rows:
        return "Chưa có người dùng nào."
    lines = ["Danh sách thành viên:"]
    for row in rows:
        enabled = "đang hoạt động" if row["enabled"] else "đã tắt"
        expires = f", hết hạn {row['expires_at']}" if row["expires_at"] else ""
        name = row["display_name"] or "Không rõ tên"
        username = f" @{row['username']}" if row["username"] else ""
        lines.append(
            f"{name}{username} ({row['channel']} {mask_user_id(row['user_id'])}) "
            f"{_role_label(row['role'])} {enabled}{expires}"
        )
    return "\n".join(lines)


def _format_blocked_users(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """
        SELECT channel, user_id, display_name, username, role, enabled
        FROM users
        WHERE role = 'blocked' OR enabled = 0
        ORDER BY channel, display_name, username, user_id
        """
    ).fetchall()
    if not rows:
        return "Hiện không có người dùng nào đang bị chặn."
    lines = ["Người dùng đang bị chặn:"]
    for row in rows:
        name = row["display_name"] or "Không rõ tên"
        username = f" @{row['username']}" if row["username"] else ""
        lines.append(f"{name}{username} ({row['channel']} {mask_user_id(row['user_id'])}) {row['role']}")
    return "\n".join(lines)


def _format_member_count(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """
        SELECT role, enabled, COUNT(*) AS count
        FROM users
        GROUP BY role, enabled
        ORDER BY role, enabled DESC
        """
    ).fetchall()
    if not rows:
        return "Hệ thống chưa có người dùng nào."
    total = sum(int(row["count"]) for row in rows)
    grouped: dict[tuple[str, int], int] = {}
    for row in rows:
        key = (_role_label(row["role"]), int(row["enabled"]))
        grouped[key] = grouped.get(key, 0) + int(row["count"])
    parts = []
    for (role_label, enabled), count in sorted(grouped.items(), key=lambda item: (item[0][0], -item[0][1])):
        status = "đang hoạt động" if enabled else "đã tắt"
        parts.append(f"{count} {role_label} {status}")
    return f"Hệ thống có {total} người dùng: " + ", ".join(parts) + "."


def _role_label(role: str | None) -> str:
    return {
        "owner": "chủ nhà",
        "admin": "quản trị viên",
        "member": "thành viên",
        "family": "thành viên",
        "viewer": "người xem",
        "guest": "khách",
        "blocked": "đã bị chặn",
    }.get(role or "", role or "không rõ vai trò")


def _find_users(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    normalized_query = normalized_text(query).lstrip("@")
    if not normalized_query:
        return []
    rows = conn.execute(
        """
        SELECT channel, user_id, display_name, username, role, enabled
        FROM users
        ORDER BY enabled DESC, role, channel, user_id
        """
    ).fetchall()
    exact: list[sqlite3.Row] = []
    contains: list[sqlite3.Row] = []
    for row in rows:
        fields = [
            row["user_id"] or "",
            row["display_name"] or "",
            row["username"] or "",
            f"{row['channel']} {row['user_id']}",
        ]
        normalized_fields = [normalized_text(field).lstrip("@") for field in fields]
        if normalized_query in normalized_fields:
            exact.append(row)
        elif any(normalized_query in field for field in normalized_fields if field):
            contains.append(row)
    return exact or contains


def _last_role_before_block(conn: sqlite3.Connection, channel: str, user_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT old_role
        FROM user_audit_log
        WHERE target_channel = ? AND target_user_id = ?
          AND action IN ('user_revoked', 'user_disabled')
          AND old_role IS NOT NULL AND old_role != ''
        ORDER BY id DESC
        LIMIT 1
        """,
        (channel, user_id),
    ).fetchone()
    if not row:
        return None
    role = normalize_role(row["old_role"])
    return role if role in VALID_ROLES else None


def _require_owner_for_owner_change(conn: sqlite3.Connection, actor, channel: str | None, user_id: str | None, role: str | None) -> None:
    if role != "owner":
        return
    if actor.role != "owner":
        raise ValueError("only owner can grant owner role")


def _is_last_enabled_owner(conn: sqlite3.Connection, channel: str, user_id: str) -> bool:
    rows = conn.execute(
        "SELECT channel, user_id FROM users WHERE role = 'owner' AND enabled = 1"
    ).fetchall()
    return len(rows) == 1 and rows[0]["channel"] == channel and rows[0]["user_id"] == user_id


def _required(value: str | None, name: str) -> str:
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _required_request_id(command: AdminCommand) -> int:
    if command.request_id is None:
        raise ValueError("request_id is required")
    return command.request_id


def _utc_after_days(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _audit(
    conn: sqlite3.Connection,
    *,
    actor_channel: str | None,
    actor_user_id: str | None,
    target_channel: str | None,
    target_user_id: str | None,
    action: str,
    old_role: str | None = None,
    new_role: str | None = None,
    metadata: dict | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO user_audit_log (
            actor_channel, actor_user_id, target_channel, target_user_id, action,
            old_role, new_role, metadata, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            actor_channel,
            actor_user_id,
            target_channel,
            target_user_id,
            action,
            old_role,
            new_role,
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )
