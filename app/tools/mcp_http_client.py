from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import settings


MCP_PROTOCOL_VERSION = "2025-06-18"


class MCPClientError(RuntimeError):
    """An MCP server or transport error safe to return to the workflow."""


def call_mcp_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call one tool on the configured Streamable HTTP MCP server.

    The companion k8s-mcp server is stateless and configured for JSON responses,
    allowing the Python 3.9 Agent process to remain independent from the MCP SDK.
    """
    session_id = _initialize()
    headers = {"Mcp-Session-Id": session_id} if session_id else {}
    _send_notification("notifications/initialized", headers)
    response = _send_request(
        "tools/call",
        2,
        {"name": tool_name, "arguments": arguments},
        headers,
    )
    result = response.get("result")
    if not isinstance(result, dict):
        raise MCPClientError(_jsonrpc_error(response) or "MCP 工具未返回有效结果。")
    if result.get("isError"):
        raise MCPClientError(_extract_text_content(result) or "MCP 工具调用失败。")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    text = _extract_text_content(result)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise MCPClientError("MCP 工具返回了无法解析的文本结果。") from error
    if not isinstance(parsed, dict):
        raise MCPClientError("MCP 工具返回的 JSON 不是对象。")
    return parsed


def _initialize() -> str:
    response, response_headers = _send_request_with_headers(
        "initialize",
        1,
        {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mec-5g-rag-agent", "version": "0.1.0"},
        },
        {},
    )
    if not isinstance(response.get("result"), dict):
        raise MCPClientError(_jsonrpc_error(response) or "MCP 初始化失败。")
    return str(response_headers.get("Mcp-Session-Id", "")).strip()


def _send_notification(method: str, headers: dict[str, str]) -> None:
    _post(
        {"jsonrpc": "2.0", "method": method},
        headers,
        expect_json=False,
    )


def _send_request(
    method: str,
    request_id: int,
    params: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    response, _ = _send_request_with_headers(method, request_id, params, headers)
    return response


def _send_request_with_headers(
    method: str,
    request_id: int,
    params: dict[str, Any],
    headers: dict[str, str],
) -> tuple[dict[str, Any], dict[str, str]]:
    response, response_headers = _post(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        headers,
        expect_json=True,
    )
    if not isinstance(response, dict):
        raise MCPClientError("MCP 服务未返回 JSON 对象。")
    return response, response_headers


def _post(
    payload: dict[str, Any],
    headers: dict[str, str],
    expect_json: bool,
) -> tuple[Any, dict[str, str]]:
    request_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        **headers,
    }
    request = Request(
        settings.k8s_mcp_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.k8s_mcp_timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            response_headers = dict(response.headers.items())
            if not expect_json:
                return None, response_headers
            if response.headers.get_content_type() != "application/json":
                raise MCPClientError("K8S MCP 必须配置为 JSON 响应的 Streamable HTTP 服务。")
            return json.loads(raw), response_headers
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise MCPClientError(f"K8S MCP 请求失败（HTTP {error.code}）：{detail}") from error
    except URLError as error:
        raise MCPClientError(f"无法连接 K8S MCP：{error.reason}") from error
    except TimeoutError as error:
        raise MCPClientError("K8S MCP 请求超时。") from error
    except json.JSONDecodeError as error:
        raise MCPClientError("K8S MCP 返回了无法解析的 JSON。") from error


def _extract_text_content(result: dict[str, Any]) -> str:
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    values = [
        str(item.get("text", ""))
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    return "\n".join(value for value in values if value)


def _jsonrpc_error(response: dict[str, Any]) -> str:
    error = response.get("error")
    if not isinstance(error, dict):
        return ""
    return str(error.get("message", ""))
