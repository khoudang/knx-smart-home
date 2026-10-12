from __future__ import annotations

import re
import unicodedata
from typing import Any

from smart_home_agent.agent_core.command_models import Intent


def parse_local(text: str) -> Intent | None:
    normalized = _normalize(text)
    if not normalized:
        return None
    ascii_normalized = _strip_accents(normalized)

    if ascii_normalized in {"xin chao", "chao", "hello", "hi", "alo", "bot oi"}:
        return Intent(intent="greeting", confidence=0.95)

    inventory_intent = _inventory_intent(ascii_normalized)
    if inventory_intent:
        return inventory_intent

    info_intent = _device_info_intent(normalized, ascii_normalized)
    if info_intent:
        return info_intent

    brightness = re.search(r"(\d{1,3})\s*(phan tram|%)", ascii_normalized)
    if brightness:
        value = int(brightness.group(1))
        device_name = normalized[: brightness.start()].strip()
        device_name = _strip_action_words(device_name)
        return Intent(
            intent="set_brightness",
            device_name=device_name or None,
            room=_extract_room(normalized),
            parameters={"brightness": value},
            confidence=0.9,
        )

    turn_on = _prefix_intent(
        "turn_on",
        normalized,
        ascii_normalized,
        ("bat", "mo", "cho toi bat", "cho toi mo", "bat giup", "mo giup", "bat dum", "mo dum"),
    )
    if turn_on:
        return turn_on
    turn_off = _prefix_intent(
        "turn_off",
        normalized,
        ascii_normalized,
        ("tat", "cho toi tat", "tat giup", "tat dum"),
    )
    if turn_off:
        return turn_off
    read_state = _prefix_intent(
        "read_state",
        normalized,
        ascii_normalized,
        ("doc", "kiem tra", "xem trang thai", "trang thai"),
    )
    if read_state:
        return read_state

    suffix_on = _suffix_intent("turn_on", normalized, ascii_normalized, ("bat len", "bat di", "sang len", "mo len"))
    if suffix_on:
        return suffix_on
    suffix_off = _suffix_intent("turn_off", normalized, ascii_normalized, ("tat di", "tat nhe", "tat dum", "tat giup"))
    if suffix_off:
        return suffix_off

    if ascii_normalized.startswith("goi scene "):
        scene_name = normalized.split(" ", 2)[2].strip()
        return Intent(intent="run_scene", scene_name=scene_name, confidence=0.9)
    return None


def validate_parameters(intent: Intent) -> None:
    if intent.intent == "set_brightness":
        value = intent.parameters.get("brightness")
        if not isinstance(value, int) or value < 0 or value > 100:
            raise ValueError("Do sang phai la so nguyen tu 0 den 100.")


def _inventory_intent(ascii_normalized: str) -> Intent | None:
    count_patterns = (
        "nha co bao nhieu thiet bi",
        "co bao nhieu thiet bi",
        "tong so thiet bi",
        "dem thiet bi",
        "hien co may thiet bi",
        "may thiet bi",
    )
    light_count_patterns = (
        "co bao nhieu den",
        "bao nhieu den",
        "may den",
    )
    list_patterns = (
        "danh sach thiet bi",
        "xem danh sach thiet bi",
        "cho toi xem danh sach thiet bi",
        "cho xem danh sach thiet bi",
        "liet ke cac thiet bi",
        "liet ke thiet bi",
        "trong nha co nhung thiet bi gi",
        "nha co nhung thiet bi gi",
        "nha minh co nhung gi",
        "co nhung thiet bi nao",
        "xem thiet bi",
    )
    status_patterns = (
        "co thiet bi nao dang bat khong",
        "nhung den nao dang bat",
        "den nao dang bat",
        "co den nao dang bat khong",
        "trang thai tat ca thiet bi",
    )
    if _contains_any(ascii_normalized, light_count_patterns):
        return Intent(intent="count_devices", parameters={"device_type": "light"}, confidence=0.95)
    if _contains_any(ascii_normalized, count_patterns):
        return Intent(intent="count_devices", confidence=0.95)
    if _contains_any(ascii_normalized, list_patterns):
        return Intent(intent="list_devices", confidence=0.95)
    if _contains_any(ascii_normalized, status_patterns):
        return Intent(intent="status_summary", confidence=0.9)
    return None


def _device_info_intent(normalized: str, ascii_normalized: str) -> Intent | None:
    suffixes = ("la thiet bi nao", "la gi", "o dau", "nam o dau", "thong tin", "mo ta", "co chuc nang gi")
    for suffix in suffixes:
        marker = f" {suffix}"
        if not ascii_normalized.endswith(marker):
            continue
        suffix_words = len(suffix.split())
        device_name = " ".join(normalized.split()[:-suffix_words]).strip()
        if _strip_accents(device_name) in {"ten", "ban ten", "bot ten", "ten cua ban"}:
            return None
        if device_name:
            return Intent(intent="device_info", device_name=device_name, confidence=0.9)
    return None


def _contains_any(ascii_normalized: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in ascii_normalized for pattern in patterns)


def _prefix_intent(intent: str, normalized: str, ascii_normalized: str, prefixes: tuple[str, ...]) -> Intent | None:
    for prefix in sorted(prefixes, key=lambda item: len(item.split()), reverse=True):
        if ascii_normalized == prefix:
            return Intent(intent=intent, device_name=None, room=None, parameters={}, confidence=0.78)
        if not ascii_normalized.startswith(prefix + " "):
            continue
        prefix_words = len(prefix.split())
        body = " ".join(normalized.split()[prefix_words:]).strip()
        body = _strip_polite_words(body)
        return Intent(intent=intent, device_name=body or None, room=_extract_room(body), parameters={}, confidence=0.86)
    return None


def _suffix_intent(intent: str, normalized: str, ascii_normalized: str, suffixes: tuple[str, ...]) -> Intent | None:
    for suffix in suffixes:
        marker = f" {suffix}"
        if not ascii_normalized.endswith(marker):
            continue
        suffix_words = len(suffix.split())
        device_name = " ".join(normalized.split()[:-suffix_words]).strip()
        device_name = _strip_polite_words(device_name)
        if not device_name:
            return None
        return Intent(intent=intent, device_name=device_name, room=_extract_room(device_name), parameters={}, confidence=0.82)
    return None


def _strip_action_words(text: str) -> str:
    ascii_text = _strip_accents(text)
    for word in ("dat ", "chinh "):
        if ascii_text.startswith(word):
            return " ".join(text.split()[1:]).strip()
    return _strip_polite_words(text)


def _strip_polite_words(text: str) -> str:
    words = text.split()
    polite = {"giup", "dum", "ho", "cho", "toi", "minh", "anh", "chi", "nhe", "di"}
    while words and _strip_accents(words[0]) in polite:
        words.pop(0)
    while words and _strip_accents(words[-1]) in polite:
        words.pop()
    return " ".join(words).strip()


def _extract_room(text: str) -> str | None:
    ascii_text = _strip_accents(text)
    match = re.search(r"\bphong\s+(.+)$", ascii_text, re.IGNORECASE)
    if not match:
        return None
    suffix_words = len(match.group(1).split())
    return " ".join(text.split()[-suffix_words:]).strip()


def _normalize(text: Any) -> str:
    return " ".join(_repair_mojibake(str(text or "")).strip().lower().split()).strip(" ?!.")


def _strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.replace("\u0111", "d").replace("\u0110", "D")


def _repair_mojibake(value: str) -> str:
    try:
        repaired = value.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return repaired if "\ufffd" not in repaired else value
