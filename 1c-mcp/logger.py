#!/usr/bin/env python3
"""Structured logging and optional telemetry for MCP tool calls."""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

DEFAULT_TELEMETRY_BASE_URL = "http://localhost:3000"
DEFAULT_PREVIEW_CHARS = 0
MAX_HTTP_TIMEOUT_SECONDS = 5

EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
PHONE_PATTERN = re.compile(r"(?:(?:\+?\d[\s\-()]*){7,}\d)")
URL_PATTERN = re.compile(r"https?://[^\s\"']+")
LONG_NUMBER_PATTERN = re.compile(r"\b\d{6,}\b")
NUMBER_PATTERN = re.compile(r"(?<!\w)([+-]?\d[\d\s.,]{2,}\d|\d{2,})(?!\w)")


@dataclass(frozen=True)
class LoggerConfig:
    enabled: bool
    print_enabled: bool
    telemetry_enabled: bool
    telemetry_base_url: str
    preview_chars: int
    client_id: str


def configure_logger() -> LoggerConfig:
    return LoggerConfig(
        enabled=parse_bool_env("MCP_LOG_ENABLED", default=True),
        print_enabled=parse_bool_env("MCP_LOG_PRINT_ENABLED", default=True),
        telemetry_enabled=parse_bool_env(
            "MCP_LOG_TELEMETRY_ENABLED",
            default=True,
        ),
        telemetry_base_url=(
            os.environ.get("MCP_LOG_TELEMETRY_URL", "").strip()
            or DEFAULT_TELEMETRY_BASE_URL
        ),
        preview_chars=parse_int_env(
            "MCP_LOG_PREVIEW_CHARS",
            default=DEFAULT_PREVIEW_CHARS,
        ),
        client_id=(
            os.environ.get("CLAUDE_SESSION_ID", "").strip()
            or os.environ.get("MCP_LOG_CLIENT_ID", "").strip()
            or "unknown-session"
        ),
    )


def log_tool_call(
    *,
    tool_name: str,
    request: dict[str, Any],
    response: object | None = None,
    error: Exception | None = None,
) -> None:
    config = configure_logger()
    if not config.enabled:
        return

    should_send_full_payload = config.preview_chars <= 0
    event = build_log_event(
        tool_name=tool_name,
        request=request,
        response=response,
        error=error,
        max_response_chars=(
            None if should_send_full_payload else 4000
        ),
    )

    if config.print_enabled:
        print(json.dumps(event, ensure_ascii=False))

    if not config.telemetry_enabled:
        return

    payload = build_trace_payload(
        client_id=config.client_id,
        user_query=(
            f"{tool_name} "
            f"{json.dumps(mask_sensitive(request), ensure_ascii=False)}"
        ),
        tool_response=json.dumps(
            event.get("response"),
            ensure_ascii=False,
            default=str,
        ),
        preview_chars=config.preview_chars,
        metadata={
            "tool": tool_name,
            "status": event["status"],
            "timestampUtc": event["timestampUtc"],
        },
    )
    send_trace_put_async(
        payload=payload,
        base_url=config.telemetry_base_url,
        client_id=config.client_id,
    )


def build_log_event(
    *,
    tool_name: str,
    request: dict[str, Any],
    response: object | None,
    error: Exception | None,
    max_response_chars: int | None = 4000,
) -> dict[str, Any]:
    return {
        "timestampUtc": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        "status": "error" if error else "ok",
        "request": mask_sensitive(request),
        "response": clip_response(response, max_chars=max_response_chars),
        "error": None if not error else str(error),
    }


def mask_sensitive(payload: dict[str, Any]) -> dict[str, Any]:
    masked = dict(payload)
    sensitive_keys = (
        "password",
        "ODATA_PASS",
        "odata_pass",
        "token",
        "authorization",
    )
    for key in sensitive_keys:
        if key in masked and masked[key] is not None:
            masked[key] = "***"
    return masked


def clip_response(
    response: object | None,
    max_chars: int | None = 4000,
) -> object:
    if response is None:
        return None
    if max_chars is None or max_chars <= 0:
        return response

    serialized = json.dumps(response, ensure_ascii=False, default=str)
    if len(serialized) <= max_chars:
        return response
    return {"preview": f"{serialized[:max_chars]}...", "truncated": True}


def parse_bool_env(key: str, *, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    return default


def parse_int_env(key: str, *, default: int) -> int:
    raw = (os.environ.get(key, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def clamp_preview_chars(preview_chars: int) -> int:
    if preview_chars < 1:
        return 1
    if preview_chars > 2000:
        return 2000
    return preview_chars


def anonymize_text(raw_text: str) -> str:
    text = raw_text or ""
    text = EMAIL_PATTERN.sub("[EMAIL]", text)
    text = PHONE_PATTERN.sub("[PHONE]", text)
    text = URL_PATTERN.sub("[URL]", text)
    text = LONG_NUMBER_PATTERN.sub("[ID]", text)
    return NUMBER_PATTERN.sub("[NUM]", text)


def shorten_text(text: str, *, preview_chars: int) -> str:
    if preview_chars <= 0:
        return text

    normalized_preview = clamp_preview_chars(preview_chars)
    if len(text) <= normalized_preview:
        return text
    return f"{text[:normalized_preview]}..."


def build_trace_url(*, base_url: str, client_id: str) -> str:
    normalized_base = (base_url or DEFAULT_TELEMETRY_BASE_URL).rstrip("/")
    if not normalized_base:
        normalized_base = DEFAULT_TELEMETRY_BASE_URL
    query = urllib.parse.urlencode(
        {"clientId": client_id or "unknown-session"}
    )
    return f"{normalized_base}/trace?{query}"


def build_trace_payload(
    *,
    client_id: str,
    user_query: str,
    tool_response: str,
    preview_chars: int = DEFAULT_PREVIEW_CHARS,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata_payload = metadata or {}
    safe_query = shorten_text(
        anonymize_text(user_query),
        preview_chars=preview_chars,
    )
    safe_response = shorten_text(
        anonymize_text(tool_response),
        preview_chars=preview_chars,
    )
    return {
        "clientId": client_id or "unknown-session",
        "timestampUtc": datetime.now(timezone.utc).isoformat(),
        "previewChars": (
            0 if preview_chars <= 0 else clamp_preview_chars(preview_chars)
        ),
        "queryPreview": safe_query,
        "responsePreview": safe_response,
        "metadata": metadata_payload,
    }


def send_trace_put(
    *,
    payload: dict[str, Any],
    base_url: str = DEFAULT_TELEMETRY_BASE_URL,
    client_id: str,
    timeout_seconds: int = MAX_HTTP_TIMEOUT_SECONDS,
) -> None:
    url = build_trace_url(base_url=base_url, client_id=client_id)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds):
            return
    except (urllib.error.URLError, urllib.error.HTTPError):
        return


def send_trace_put_async(
    *,
    payload: dict[str, Any],
    base_url: str = DEFAULT_TELEMETRY_BASE_URL,
    client_id: str,
    timeout_seconds: int = MAX_HTTP_TIMEOUT_SECONDS,
) -> threading.Thread:
    worker = threading.Thread(
        target=send_trace_put,
        kwargs={
            "payload": payload,
            "base_url": base_url,
            "client_id": client_id,
            "timeout_seconds": timeout_seconds,
        },
        daemon=True,
    )
    worker.start()
    return worker
