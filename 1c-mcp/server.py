#!/usr/bin/env python3
"""MCP server for 1C OData probing and querying."""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP
from logger import log_tool_call

STANDARD_ODATA_SUFFIX = "/odata/standard.odata"
DEFAULT_TIMEOUT_SECONDS = 60
RESOURCE_SAFE_CHARS = "/$(),='"
QUERY_SAFE_CHARS = "$(),='/:"

ENV_HELP = {
    "ODATA_HOST": (
        "Base URL of the 1C infobase, without /odata/standard.odata. "
        "Example: https://1c.example.com/Trade"
    ),
    "ODATA_USER": "1C user login with OData access",
    "ODATA_PASS": "Password for ODATA_USER",
}

mcp = FastMCP("1c-odata")


@dataclass
class HttpResponse:
    status_code: int
    url: str
    text: str


def main() -> None:
    print("1c-odata MCP server started (stdio transport)", file=sys.stderr, flush=True)
    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        print("1c-odata MCP server stopped by Ctrl+C", file=sys.stderr, flush=True)


@mcp.tool()
def probe_odata(
    host: str | None = None,
    user: str | None = None,
    password: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    verify_tls: bool = False,
) -> dict[str, Any]:
    """Check OData connectivity and credentials for a 1C infobase."""
    request_payload = {
        "host": host,
        "user": user,
        "password": password,
        "timeout_seconds": timeout_seconds,
        "verify_tls": verify_tls,
    }
    try:
        resolved_host, resolved_user, resolved_password = resolve_credentials(
            host=host,
            user=user,
            password=password,
        )
        service_root = build_service_root(resolved_host)
        url = f"{service_root}/?$format=json"
        response = perform_get(
            url=url,
            user=resolved_user,
            password=resolved_password,
            timeout_seconds=timeout_seconds,
            verify_tls=verify_tls,
        )
        payload = safe_json(response)
        result = {
            "status": "ok" if response.status_code == 200 else "error",
            "status_code": response.status_code,
            "service_root": service_root,
            "url": response.url,
            "body_keys": (
                sorted(payload.keys()) if isinstance(payload, dict) else []
            ),
            "body": payload,
        }
        log_tool_call(
            tool_name="probe_odata",
            request=request_payload,
            response=result,
        )
        return result
    except Exception as error:
        log_tool_call(
            tool_name="probe_odata",
            request=request_payload,
            error=error,
        )
        raise


@mcp.tool()
def get_odata(
    resource: str,
    query: str = "",
    host: str | None = None,
    user: str | None = None,
    password: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    verify_tls: bool = False,
    parse_json: bool = True,
) -> dict[str, Any]:
    """Run a GET request against /odata/standard.odata/{resource}."""
    request_payload = {
        "resource": resource,
        "query": query,
        "host": host,
        "user": user,
        "password": password,
        "timeout_seconds": timeout_seconds,
        "verify_tls": verify_tls,
        "parse_json": parse_json,
    }
    try:
        resolved_host, resolved_user, resolved_password = resolve_credentials(
            host=host,
            user=user,
            password=password,
        )
        service_root = build_service_root(resolved_host)
        url = build_request_url(
            service_root=service_root,
            resource=resource,
            query=query,
        )
        response = perform_get(
            url=url,
            user=resolved_user,
            password=resolved_password,
            timeout_seconds=timeout_seconds,
            verify_tls=verify_tls,
        )
        body: object = safe_json(response) if parse_json else response.text
        result = {
            "status": "ok" if response.status_code < 400 else "error",
            "status_code": response.status_code,
            "url": response.url,
            "body": body,
        }
        log_tool_call(
            tool_name="get_odata",
            request=request_payload,
            response=result,
        )
        return result
    except Exception as error:
        log_tool_call(
            tool_name="get_odata",
            request=request_payload,
            error=error,
        )
        raise


@mcp.tool()
def list_entity_sets(
    host: str | None = None,
    user: str | None = None,
    password: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    verify_tls: bool = False,
) -> dict[str, Any]:
    """List entity set names from the OData service document."""
    request_payload = {
        "host": host,
        "user": user,
        "password": password,
        "timeout_seconds": timeout_seconds,
        "verify_tls": verify_tls,
    }
    try:
        resolved_host, resolved_user, resolved_password = resolve_credentials(
            host=host,
            user=user,
            password=password,
        )
        service_root = build_service_root(resolved_host)
        url = build_request_url(
            service_root=service_root,
            resource="",
            query="$format=json",
        )
        raw_response = perform_get(
            url=url,
            user=resolved_user,
            password=resolved_password,
            timeout_seconds=timeout_seconds,
            verify_tls=verify_tls,
        )
        body = safe_json(raw_response)
        names = extract_entity_set_names(body)
        result = {
            "status": "ok" if raw_response.status_code < 400 else "error",
            "status_code": raw_response.status_code,
            "url": raw_response.url,
            "entity_sets": names,
            "count": len(names),
        }
        log_tool_call(
            tool_name="list_entity_sets",
            request=request_payload,
            response=result,
        )
        return result
    except Exception as error:
        log_tool_call(
            tool_name="list_entity_sets",
            request=request_payload,
            error=error,
        )
        raise


def resolve_credentials(
    *,
    host: str | None,
    user: str | None,
    password: str | None,
) -> tuple[str, str, str]:
    resolved_host = (host or os.environ.get("ODATA_HOST", "")).strip()
    resolved_user = (user or os.environ.get("ODATA_USER", "")).strip()
    resolved_password = (password or os.environ.get("ODATA_PASS", "")).strip()
    missing_keys: list[str] = []
    if not resolved_host:
        missing_keys.append("ODATA_HOST")
    if not resolved_user:
        missing_keys.append("ODATA_USER")
    if not resolved_password:
        missing_keys.append("ODATA_PASS")
    if missing_keys:
        raise ValueError(build_missing_env_message(missing_keys))
    return resolved_host, resolved_user, resolved_password


def build_missing_env_message(missing_keys: list[str]) -> str:
    lines = ["Missing required OData credentials:"]
    lines.extend(f"- {key}: {ENV_HELP[key]}" for key in missing_keys)
    lines.append("")
    lines.append("Set these as tool arguments or environment variables.")
    return "\n".join(lines)


def build_service_root(host: str) -> str:
    normalized_host = host.rstrip("/")
    if normalized_host.endswith(STANDARD_ODATA_SUFFIX):
        return normalized_host
    return f"{normalized_host}{STANDARD_ODATA_SUFFIX}"


def build_request_url(*, service_root: str, resource: str, query: str) -> str:
    normalized_resource = resource.strip()
    if not normalized_resource:
        url = service_root
    else:
        encoded_resource = encode_resource_path(normalized_resource)
        url = f"{service_root}/{encoded_resource}"

    encoded_query = encode_query_string(query)
    if not encoded_query:
        return url

    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{encoded_query}"


def encode_resource_path(resource: str) -> str:
    normalized_resource = resource.lstrip("/")
    return urllib.parse.quote(normalized_resource, safe=RESOURCE_SAFE_CHARS)


def encode_query_string(query: str) -> str:
    normalized_query = query.lstrip("?").strip()
    if not normalized_query:
        return ""

    query_pairs = urllib.parse.parse_qsl(
        normalized_query,
        keep_blank_values=True,
    )
    if not query_pairs:
        return urllib.parse.quote(normalized_query, safe="$&=(),'/:")

    return urllib.parse.urlencode(
        query_pairs,
        doseq=True,
        quote_via=urllib.parse.quote,
        safe=QUERY_SAFE_CHARS,
    )


def perform_get(
    *,
    url: str,
    user: str,
    password: str,
    timeout_seconds: int,
    verify_tls: bool,
) -> HttpResponse:
    token = b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": (
                "application/json, application/xml;q=0.9, "
                "text/plain;q=0.8"
            ),
            "Authorization": f"Basic {token}",
        },
        method="GET",
    )
    context = None
    if not verify_tls:
        context = ssl._create_unverified_context()  # noqa: S323

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout_seconds,
            context=context,
        ) as response:
            body = response.read().decode("utf-8", errors="replace")
            return HttpResponse(
                status_code=response.getcode(),
                url=response.geturl(),
                text=body,
            )
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        return HttpResponse(
            status_code=error.code,
            url=error.geturl(),
            text=body,
        )
    except urllib.error.URLError as error:
        return HttpResponse(
            status_code=599,
            url=url,
            text=f"Connection failed for {url}\n{error}",
        )


def safe_json(response: HttpResponse) -> object:
    try:
        return json.loads(response.text)
    except ValueError:
        return {"raw_text": response.text}


def extract_entity_set_names(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []

    if isinstance(payload.get("value"), list):
        names = [
            entry["name"]
            for entry in payload["value"]
            if isinstance(entry, dict) and isinstance(entry.get("name"), str)
        ]
        return sorted(names, key=str.lower)

    if isinstance(payload.get("d"), dict) and isinstance(
        payload["d"].get("EntitySets"),
        list,
    ):
        names = [
            name for name in payload["d"]["EntitySets"] if isinstance(name, str)
        ]
        return sorted(names, key=str.lower)

    return []


if __name__ == "__main__":
    main()
