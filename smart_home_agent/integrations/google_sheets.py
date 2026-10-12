from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


EXPECTED_SERVICE_ACCOUNT = os.environ.get("GOOGLE_EXPECTED_SERVICE_ACCOUNT", "")
SCOPES = ("https://www.googleapis.com/auth/spreadsheets.readonly",)


def valid_service_account_info(info: dict[str, Any], expected_account: str = "") -> bool:
    """Validate local account shape, optionally pinned by the operator."""
    return bool(info.get("type") == "service_account" and info.get("client_email")
                and info.get("private_key")
                and (not expected_account or info["client_email"] == expected_account))


class GoogleSheetError(RuntimeError):
    def __init__(self, code: str, message: str, *, visible_tabs: list[str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.visible_tabs = visible_tabs or []


def google_credentials_path() -> Path | None:
    value = os.environ.get("GOOGLE_CREDENTIALS_FILE") or os.environ.get("GOOGLE_CREDENTIALS_PATH")
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def load_service_account_info(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Google credentials JSON must be an object")
    return data


def build_sheets_service(credentials_path: Path):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    try:
        credentials = service_account.Credentials.from_service_account_file(str(credentials_path), scopes=SCOPES)
        return build("sheets", "v4", credentials=credentials, cache_discovery=False)
    except Exception as exc:
        raise GoogleSheetError("invalid_credentials", "invalid Google service-account credentials") from exc


def assert_tab_exists(service, sheet_id: str, tab_name: str) -> None:
    metadata = _execute_google(
        service.spreadsheets().get(spreadsheetId=sheet_id, fields="sheets.properties.title")
    )
    titles = [sheet["properties"]["title"] for sheet in metadata.get("sheets", [])]
    if tab_name not in titles:
        raise GoogleSheetError("tab_not_found", f"Google Sheet tab not found: {tab_name}", visible_tabs=titles)


def fetch_tab_rows(service, sheet_id: str, tab_name: str) -> list[dict[str, Any]]:
    result = _execute_google(service.spreadsheets().values().get(spreadsheetId=sheet_id, range=f"'{tab_name}'"))
    values = result.get("values", [])
    if not values:
        return []
    headers = [str(cell).strip() for cell in values[0]]
    rows: list[dict[str, Any]] = []
    for values_row in values[1:]:
        row = {header: values_row[idx] if idx < len(values_row) else "" for idx, header in enumerate(headers) if header}
        rows.append(row)
    return rows


def classify_google_exception(exc: Exception) -> GoogleSheetError:
    status = getattr(getattr(exc, "resp", None), "status", None)
    payload = _google_error_payload(exc)
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    message = str(error.get("message") or exc).lower()
    api_reason = _google_error_reasons(error)
    api_status = str(error.get("status") or "").upper()

    if "SERVICE_DISABLED" in api_reason or "api" in message and ("disabled" in message or "not been used" in message):
        return GoogleSheetError("api_not_enabled", "Google Sheets API is not enabled")
    if "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in api_reason or "insufficient" in message and "scope" in message:
        return GoogleSheetError("insufficient_scope", "Google credential scope is insufficient")
    if "API_KEY_SERVICE_BLOCKED" in api_reason:
        return GoogleSheetError("api_key_service_blocked", "Google API key service is blocked")
    if "IAM_PERMISSION_DENIED" in api_reason:
        return GoogleSheetError("iam_permission_denied", "Google IAM permission denied")
    if "invalid_grant" in message or "unauthorized" in message or status == 401:
        return GoogleSheetError("invalid_credentials", "invalid Google credentials")
    if status == 403 or api_status == "PERMISSION_DENIED" or "permission" in message or "forbidden" in message:
        return GoogleSheetError("permission_denied", "Google Sheet permission denied")
    if status == 404 or api_status == "NOT_FOUND" or "not found" in message:
        return GoogleSheetError("sheet_not_found", "Google Sheet not found")
    return GoogleSheetError("google_access_error", type(exc).__name__)


def _execute_google(request):
    try:
        return request.execute()
    except Exception as exc:
        raise classify_google_exception(exc) from exc


def _google_error_payload(exc: Exception) -> dict[str, Any]:
    content = getattr(exc, "content", b"")
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError:
            content = ""
    if not isinstance(content, str) or not content:
        return {}
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _google_error_reasons(error: dict[str, Any]) -> set[str]:
    reasons: set[str] = set()
    for item in error.get("details", []) if isinstance(error.get("details"), list) else []:
        if not isinstance(item, dict):
            continue
        reason = item.get("reason")
        if reason:
            reasons.add(str(reason).upper())
        metadata = item.get("metadata")
        if isinstance(metadata, dict) and metadata.get("reason"):
            reasons.add(str(metadata["reason"]).upper())
    for item in error.get("errors", []) if isinstance(error.get("errors"), list) else []:
        if isinstance(item, dict) and item.get("reason"):
            reasons.add(str(item["reason"]).upper())
    return reasons
