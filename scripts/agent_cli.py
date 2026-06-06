import argparse
import json
import os
import sys
from typing import Any

try:
    from scripts.smoke_test import (
        MCP_PROTOCOL_VERSION,
        _assert_no_error,
        _delete_session,
        _find_tool_name,
        _notify,
        _post,
    )
except ModuleNotFoundError:
    from smoke_test import (
        MCP_PROTOCOL_VERSION,
        _assert_no_error,
        _delete_session,
        _find_tool_name,
        _notify,
        _post,
    )


def _extract_tool_payload(response: dict[str, Any]) -> Any:
    content = response.get("result", {}).get("content", [])
    if not content:
        return response

    texts = [
        item.get("text")
        for item in content
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    ]
    if not texts:
        return response
    if len(texts) > 1:
        return texts

    text = texts[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _format_tool_result(response: dict[str, Any], raw: bool = False) -> str:
    if raw:
        return json.dumps(response, indent=2, ensure_ascii=False)

    payload = _extract_tool_payload(response)
    if not isinstance(payload, dict):
        return str(payload)

    lines: list[str] = []
    answer = payload.get("answer") or payload.get("summary")
    if answer:
        lines.append(str(answer))

    sql = payload.get("sql")
    if sql:
        lines.extend(["", "SQL:", str(sql)])

    metadata = []
    if "row_count" in payload:
        metadata.append(f"rows={payload['row_count']}")
    if "relations_used" in payload:
        metadata.append(f"relations={', '.join(payload['relations_used'])}")
    if payload.get("status"):
        metadata.append(f"status={payload['status']}")
    if metadata:
        lines.extend(["", "Meta: " + " | ".join(metadata)])

    if lines:
        return "\n".join(lines)
    return json.dumps(payload, indent=2, ensure_ascii=False)


class AgentCli:
    def __init__(self, gateway_url: str, token: str, target_name: str | None) -> None:
        self.gateway_url = gateway_url
        self.token = token
        self.target_name = target_name
        self.session_id: str | None = None
        self.tool_name: str | None = None
        self.raw = False
        self.max_rows = int(os.environ.get("AGENT_CLI_MAX_ROWS", "10"))
        self.request_id = 1

    def connect(self) -> None:
        initialized = _post(
            self.gateway_url,
            self.token,
            {
                "jsonrpc": "2.0",
                "id": "initialize",
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "data-agent-cli", "version": "0.1.0"},
                },
            },
        )
        _assert_no_error(initialized.payload)
        self.session_id = initialized.headers.get("Mcp-Session-Id")

        _notify(
            self.gateway_url,
            self.token,
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            self.session_id,
        )

        listed = self.call_method("tools/list", {})
        self.tool_name = _find_tool_name(listed, self.target_name)

    def close(self) -> None:
        _delete_session(self.gateway_url, self.token, self.session_id)

    def call_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.request_id += 1
        payload = _post(
            self.gateway_url,
            self.token,
            {
                "jsonrpc": "2.0",
                "id": self.request_id,
                "method": method,
                "params": params,
            },
            self.session_id,
        ).payload
        _assert_no_error(payload)
        return payload

    def ask(self, question: str) -> dict[str, Any]:
        if not self.tool_name:
            raise RuntimeError("CLI is not connected")
        return self.call_method(
            "tools/call",
            {
                "name": self.tool_name,
                "arguments": {"question": question, "max_rows": self.max_rows},
            },
        )

    def print_help(self) -> None:
        print(
            "\n".join(
                [
                    "Commands:",
                    "  :help          Show this help",
                    "  :tools         Print selected MCP tool name",
                    "  :max-rows N    Set query row limit for subsequent questions",
                    "  :raw           Toggle raw JSON output",
                    "  :quit          Close the MCP session and exit",
                ]
            )
        )

    def handle_command(self, command: str) -> bool:
        parts = command.split()
        name = parts[0].lower()
        if name in {":q", ":quit", ":exit"}:
            return False
        if name == ":help":
            self.print_help()
        elif name == ":tools":
            print(self.tool_name)
        elif name == ":raw":
            self.raw = not self.raw
            print(f"raw={str(self.raw).lower()}")
        elif name == ":max-rows":
            if len(parts) != 2 or not parts[1].isdigit() or int(parts[1]) < 1:
                print("Usage: :max-rows N")
            else:
                self.max_rows = int(parts[1])
                print(f"max_rows={self.max_rows}")
        else:
            print("Unknown command. Use :help.")
        return True

    def repl(self) -> None:
        print("Data Agent CLI. Use :help for commands, :quit to exit.")
        print(f"Tool: {self.tool_name}")
        while True:
            try:
                question = input("\nagent> ").strip()
            except EOFError:
                print()
                break
            if not question:
                continue
            if question.startswith(":"):
                if not self.handle_command(question):
                    break
                continue
            try:
                response = self.ask(question)
                print(_format_tool_result(response, self.raw))
            except Exception as exc:
                print(f"Request failed: {exc}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway-url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--target-name")
    args = parser.parse_args()

    cli = AgentCli(args.gateway_url, args.token, args.target_name)
    try:
        cli.connect()
        cli.repl()
    finally:
        cli.close()


if __name__ == "__main__":
    main()
