import argparse
import json
import os
import sys
import urllib.request
from typing import Any


def _decode_response(body: bytes) -> dict[str, Any]:
    """Decode JSON directly or from a streamable HTTP event stream."""

    text = body.decode()
    if text.lstrip().startswith("{"):
        return json.loads(text)

    payloads: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            payloads.append(json.loads(line.removeprefix("data:").strip()))
    if not payloads:
        raise RuntimeError("No JSON-RPC payload found in response")
    return payloads[-1]


def _post(gateway_url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        gateway_url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return _decode_response(response.read())


def _assert_no_error(payload: dict[str, Any]) -> None:
    if "error" in payload:
        raise RuntimeError(json.dumps(payload["error"], ensure_ascii=False))


def _find_tool_name(payload: dict[str, Any]) -> str:
    tools = payload.get("result", {}).get("tools", [])
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
    args = parser.parse_args()

    listed = _post(
        args.gateway_url,
        args.token,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    _assert_no_error(listed)
    tool_name = _find_tool_name(listed)

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
    )
    _assert_no_error(called)
    if not _contains_ok_status(called):
        raise RuntimeError("ask_database did not return status ok")

    print("End-to-end smoke test passed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        raise
