import json

from scripts.smoke_test import _contains_ok_status, _decode_response, _find_tool_name


def test_decodes_event_stream_response() -> None:
    payload = {"jsonrpc": "2.0", "result": {"tools": []}}
    body = f"event: message\ndata: {json.dumps(payload)}\n\n".encode()

    assert _decode_response(body) == payload


def test_finds_prefixed_gateway_tool_name() -> None:
    payload = {"result": {"tools": [{"name": "data-agent___ask_database"}]}}

    assert _find_tool_name(payload) == "data-agent___ask_database"


def test_detects_nested_tool_status() -> None:
    payload = {"result": {"content": [{"text": '{"status": "ok"}'}]}}

    assert _contains_ok_status(payload)
