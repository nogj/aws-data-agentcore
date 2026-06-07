import argparse
from dataclasses import dataclass
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

MCP_PROTOCOL_VERSION = "2025-06-18"


@dataclass(frozen=True)
class JsonRpcResponse:
    payload: dict[str, Any]
    headers: dict[str, str]


def _decode_json_body(body: bytes) -> dict[str, Any]:
    return json.loads(body.decode())


def _read_response(response: Any, request_id: Any) -> dict[str, Any]:
    """Read only the expected JSON-RPC payload, then let the caller close the stream."""

    content_type = response.headers.get("Content-Type", "")
    if "text/event-stream" not in content_type:
        return _decode_json_body(response.read())

    while True:
        line = response.readline()
        if not line:
            break
        text = line.decode().strip()
        if not text.startswith("data:"):
            continue
        payload = json.loads(text.removeprefix("data:").strip())
        if payload.get("id") == request_id or "error" in payload:
            return payload

    raise RuntimeError("No matching JSON-RPC payload found in response")


def _headers(token: str, session_id: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        "Connection": "close",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return headers


def _header(headers: dict[str, str], name: str) -> str | None:
    """Return a response header using case-insensitive lookup."""

    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _post(
    gateway_url: str,
    token: str,
    payload: dict[str, Any],
    session_id: str | None = None,
) -> JsonRpcResponse:
    request = urllib.request.Request(
        gateway_url,
        data=json.dumps(payload).encode(),
        headers=_headers(token, session_id),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return JsonRpcResponse(
            payload=_read_response(response, payload.get("id")),
            headers=dict(response.headers),
        )


def _delete_session(gateway_url: str, token: str, session_id: str | None) -> None:
    if not session_id:
        return
    request = urllib.request.Request(
        gateway_url,
        headers=_headers(token, session_id),
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            return
    except urllib.error.HTTPError as exc:
        if exc.code in {202, 204, 404, 405}:
            return
        raise


def _notify(
    gateway_url: str,
    token: str,
    payload: dict[str, Any],
    session_id: str | None = None,
) -> None:
    request = urllib.request.Request(
        gateway_url,
        data=json.dumps(payload).encode(),
        headers=_headers(token, session_id),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            return
    except urllib.error.HTTPError as exc:
        if exc.code in {202, 204}:
            return
        raise


def _assert_no_error(payload: dict[str, Any]) -> None:
    if "error" in payload:
        raise RuntimeError(json.dumps(payload["error"], ensure_ascii=False))


def _find_tool_name(payload: dict[str, Any], target_name: str | None = None) -> str:
    tools = payload.get("result", {}).get("tools", [])
    if target_name:
        expected = f"{target_name}___ask_database"
        for tool in tools:
            name = tool.get("name", "")
            if name == expected:
                return name
    for tool in tools:
        name = tool.get("name", "")
        if name == "ask_database" or name.endswith("___ask_database"):
            return name
    raise RuntimeError("ask_database tool was not listed by Gateway")


def _contains_ok_status(value: Any) -> bool:
    if isinstance(value, dict):
        return value.get("status") == "ok" or any(
            _contains_ok_status(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_ok_status(item) for item in value)
    if isinstance(value, str):
        try:
            return _contains_ok_status(json.loads(value))
        except json.JSONDecodeError:
            return '"status": "ok"' in value or "'status': 'ok'" in value
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway-url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--target-name")
    args = parser.parse_args()

    session_id: str | None = None
    try:
        initialized = _post(
            args.gateway_url,
            args.token,
            {
                "jsonrpc": "2.0",
                "id": "initialize",
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "data-agent-smoke-test", "version": "1.0.0"},
                },
            },
        )
        _assert_no_error(initialized.payload)
        session_id = _header(initialized.headers, "Mcp-Session-Id")

        _notify(
            args.gateway_url,
            args.token,
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            session_id,
        )

        listed = _post(
            args.gateway_url,
            args.token,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            session_id,
        ).payload
        _assert_no_error(listed)
        tool_name = _find_tool_name(listed, args.target_name)

        called = _post(
            args.gateway_url,
            args.token,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": {
                        "question": args.question,
                        "max_rows": int(os.environ.get("SMOKE_MAX_ROWS", "1")),
                    },
                },
            },
            session_id,
        ).payload
        _assert_no_error(called)
        if not _contains_ok_status(called):
            raise RuntimeError("ask_database did not return status ok")
        print(json.dumps(called, indent=4))
        print("End-to-end smoke test passed")
    finally:
        _delete_session(args.gateway_url, args.token, session_id)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        raise
