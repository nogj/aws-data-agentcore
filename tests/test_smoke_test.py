import json

from scripts.smoke_test import _contains_ok_status, _find_tool_name, _read_response


class FakeResponse:
    def __init__(self, lines: list[bytes]):
        self.headers = {"Content-Type": "text/event-stream"}
        self._lines = iter(lines)

    def readline(self) -> bytes:
        return next(self._lines, b"")

    def read(self) -> bytes:
        raise AssertionError("event streams should be read line by line")


def test_decodes_event_stream_response() -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
    response = FakeResponse(
        [
            b"event: message\n",
            f"data: {json.dumps(payload)}\n".encode(),
            b"\n",
        ]
    )

    assert _read_response(response, 1) == payload


def test_finds_prefixed_gateway_tool_name() -> None:
    payload = {"result": {"tools": [{"name": "data-agent___ask_database"}]}}

    assert _find_tool_name(payload) == "data-agent___ask_database"


def test_detects_nested_tool_status() -> None:
    payload = {"result": {"content": [{"text": '{"status": "ok"}'}]}}

    assert _contains_ok_status(payload)
