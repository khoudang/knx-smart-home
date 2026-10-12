from __future__ import annotations

import json
import re
import sqlite3
import unicodedata


def normalize_text(text: str | None) -> str:
    compact = " ".join(repair_mojibake(str(text or "")).strip().lower().split())
    decomposed = unicodedata.normalize("NFKD", compact)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    normalized = re.sub(
        r"\s+",
        " ",
        stripped.replace("đ", "d").replace("Đ", "D").replace("Ä‘", "d").replace("Ä", "D"),
    ).strip()
    return normalized.strip(" ?!.")


def repair_mojibake(value: str) -> str:
    try:
        repaired = value.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return repaired if "�" not in repaired and "ï¿½" not in repaired else value


def registry_names(conn: sqlite3.Connection) -> list[str]:
    try:
        rows = conn.execute(
            """
            SELECT device_id, canonical_name, aliases, room
            FROM devices
            WHERE enabled = 1 AND sync_status != 'deleted_from_sheet'
            """
        ).fetchall()
    except sqlite3.Error:
        return []
    names: list[str] = []
    for row in rows:
        names.extend([row["device_id"], row["canonical_name"], row["room"]])
        try:
            aliases = json.loads(row["aliases"] or "[]")
        except (TypeError, json.JSONDecodeError):
            aliases = []
        names.extend(str(alias) for alias in aliases)
    return names


def phrase_in_text(phrase: str, text: str) -> bool:
    if not phrase:
        return False
    return re.search(rf"(^|\W){re.escape(phrase)}($|\W)", text) is not None


def is_short_negative_or_insult(text: str) -> bool:
    return normalize_text(text) in {"ngu", "do", "chan", "te"}


def clarification_reply() -> str:
    return "Mình chưa hiểu rõ ý bạn. Bạn có thể nói cụ thể hơn không?"


def is_weather_query(text: str) -> bool:
    normalized = normalize_text(text)
    if any(term in normalized for term in ("tren web", "tim web", "tim tren web", "tra cuu web", "tra cuu tren web", "google")):
        return False
    return any(
        term in normalized
        for term in (
            "thoi tiet",
            "du bao",
            "nhiet do",
            "co mua khong",
            "mua khong",
            "troi mua",
            "nang nong",
            "troi nong",
        )
    )


def looks_like_home_automation(conn: sqlite3.Connection, text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    if normalized in {"co", "khong", "ko", "k", "ngu", "do", "chan", "dong y", "tu choi"}:
        return False
    if is_weather_query(text):
        return False
    terms = (
        "bat ",
        "tat ",
        "mo ",
        "dong ",
        "khoa ",
        "mo khoa ",
        "tang ",
        "giam ",
        "dat nhiet do",
        "dim ",
        "chinh ",
        "doc ",
        "kiem tra ",
        "trang thai",
        "dang bat",
        "dang tat",
        "danh sach thiet bi",
        "liet ke thiet bi",
        "bao nhieu thiet bi",
        "may thiet bi",
        "tong so thiet bi",
        "trong nha co nhung thiet bi",
        "bao nhieu den",
    )
    if any(term in normalized for term in terms):
        return True
    return any(
        len(name_norm := normalize_text(name)) >= 3 and phrase_in_text(name_norm, normalized)
        for name in registry_names(conn)
    )
