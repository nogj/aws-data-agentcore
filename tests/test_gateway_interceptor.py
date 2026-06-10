import base64
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class CloudFormationLoader(yaml.SafeLoader):
    pass


def _construct_cfn_tag(loader, _tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


CloudFormationLoader.add_multi_constructor("!", _construct_cfn_tag)


def _load_interceptor_handler():
    with open(ROOT / "infrastructure/bootstrap.yaml", encoding="utf-8") as handle:
        template = yaml.load(handle, Loader=CloudFormationLoader)
    source = template["Resources"]["ScopePropagationFunction"]["Properties"]["Code"][
        "ZipFile"
    ]
    namespace = {}
    exec(source, namespace)
    return namespace["handler"]


def _jwt(claims: dict[str, object]) -> str:
    def encode(value: dict[str, object]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode(claims)}."


def _payload(token: str) -> dict[str, object]:
    raw = token.split(".")[1]
    raw += "=" * (-len(raw) % 4)
    return json.loads(base64.urlsafe_b64decode(raw))


def test_inline_interceptor_ignores_roles_in_scopes_mode(monkeypatch) -> None:
    handler = _load_interceptor_handler()
    handler.__globals__["_SECRET_CACHE"] = "test-secret"
    monkeypatch.setenv("REQUIRED_SCOPE", "data:read")
    monkeypatch.setenv("ACCEPTED_CLAIMS", "scope,scp")
    monkeypatch.setenv("IDENTITY_CLAIMS", "sub,preferred_username")
    monkeypatch.setenv("HEADER_SIGNING_SECRET_ARN", "secret-arn")
    monkeypatch.setenv("INTERNAL_CONTEXT_ISSUER", "data-agent-gateway")
    monkeypatch.setenv("INTERNAL_CONTEXT_DEFAULT_AUDIENCE", "runtime:data-agent")
    monkeypatch.setenv("INTERNAL_CONTEXT_TTL_SECONDS", "300")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "data-agent-scope-propagation-test")

    event = {
        "mcp": {
            "gatewayRequest": {
                "headers": {
                    "authorization": "Bearer "
                    + _jwt(
                        {
                            "scp": "data:read",
                            "roles": ["data:sql:read"],
                            "sub": "user-1",
                            "preferred_username": "ana@example.com",
                        }
                    ),
                    "Mcp-Session-Id": "client-controlled-session",
                    "x-data-agent-grants": "data:sql:read",
                    "x-data-agent-signature": "forged",
                    "x-data-agent-context": "forged",
                },
                "body": {"id": "request-1"},
            }
        }
    }

    response = handler(event, None)
    headers = response["mcp"]["transformedGatewayRequest"]["headers"]
    payload = _payload(headers["x-data-agent-context"])

    assert payload["grants"] == ["data:read"]
    assert payload["identity"] == {
        "sub": "user-1",
        "preferred_username": "ana@example.com",
    }
    assert payload["aud"] == "runtime:data-agent"
    assert headers["x-data-agent-context"] != "forged"
    assert headers["Mcp-Session-Id"].startswith("aff-")
    assert headers["Mcp-Session-Id"] != "client-controlled-session"
    assert "authorization" not in {key.lower() for key in headers}
    assert "x-data-agent-grants" not in {key.lower() for key in headers}
    assert "x-data-agent-signature" not in {key.lower() for key in headers}
    assert "data:sql:read" not in payload["grants"]


def test_inline_interceptor_derives_stable_affinity_session(monkeypatch) -> None:
    handler = _load_interceptor_handler()
    handler.__globals__["_SECRET_CACHE"] = "test-secret"
    monkeypatch.setenv("REQUIRED_SCOPE", "data:read")
    monkeypatch.setenv("ACCEPTED_CLAIMS", "scope,scp")
    monkeypatch.setenv("IDENTITY_CLAIMS", "sub,preferred_username")
    monkeypatch.setenv("HEADER_SIGNING_SECRET_ARN", "secret-arn")
    monkeypatch.setenv("INTERNAL_CONTEXT_DEFAULT_AUDIENCE", "runtime:data-agent")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "data-agent-scope-propagation-test")

    def event_for(sub: str) -> dict[str, object]:
        return {
            "mcp": {
                "gatewayRequest": {
                    "headers": {
                        "authorization": "Bearer "
                        + _jwt(
                            {
                                "scp": "data:read",
                                "sub": sub,
                                "preferred_username": f"{sub}@example.com",
                            }
                        )
                    },
                    "body": {"id": "request-1"},
                }
            }
        }

    first = handler(event_for("user-1"), None)["mcp"]["transformedGatewayRequest"][
        "headers"
    ]["Mcp-Session-Id"]
    second = handler(event_for("user-1"), None)["mcp"]["transformedGatewayRequest"][
        "headers"
    ]["Mcp-Session-Id"]
    other = handler(event_for("user-2"), None)["mcp"]["transformedGatewayRequest"][
        "headers"
    ]["Mcp-Session-Id"]

    assert first == second
    assert first != other


def test_inline_interceptor_derives_audience_from_tool_target(monkeypatch) -> None:
    handler = _load_interceptor_handler()
    handler.__globals__["_SECRET_CACHE"] = "test-secret"
    monkeypatch.setenv("REQUIRED_SCOPE", "data:read")
    monkeypatch.setenv("ACCEPTED_CLAIMS", "scope,scp")
    monkeypatch.setenv("IDENTITY_CLAIMS", "sub,preferred_username")
    monkeypatch.setenv("HEADER_SIGNING_SECRET_ARN", "secret-arn")
    monkeypatch.setenv("INTERNAL_CONTEXT_DEFAULT_AUDIENCE", "runtime:data-agent")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "data-agent-scope-propagation-test")

    response = handler(
        {
            "mcp": {
                "gatewayRequest": {
                    "headers": {
                        "authorization": "Bearer "
                        + _jwt(
                            {
                                "scp": "data:read",
                                "sub": "user-1",
                                "preferred_username": "ana@example.com",
                            }
                        )
                    },
                    "body": {
                        "id": "request-1",
                        "method": "tools/call",
                        "params": {"name": "cmdb___ask_database"},
                    },
                }
            }
        },
        None,
    )

    token = response["mcp"]["transformedGatewayRequest"]["headers"][
        "x-data-agent-context"
    ]

    assert _payload(token)["aud"] == "runtime:cmdb"
