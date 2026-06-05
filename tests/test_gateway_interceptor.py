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


def test_inline_interceptor_ignores_roles_in_scopes_mode(monkeypatch) -> None:
    handler = _load_interceptor_handler()
    handler.__globals__["_SECRET_CACHE"] = "test-secret"
    monkeypatch.setenv("REQUIRED_SCOPE", "data:read")
    monkeypatch.setenv("ACCEPTED_CLAIMS", "scope,scp")
    monkeypatch.setenv("IDENTITY_CLAIMS", "sub,preferred_username")
    monkeypatch.setenv("HEADER_SIGNING_SECRET_ARN", "secret-arn")

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
                    "x-data-agent-grants": "data:sql:read",
                    "x-data-agent-signature": "forged",
                },
                "body": {"id": "request-1"},
            }
        }
    }

    response = handler(event, None)
    headers = response["mcp"]["transformedGatewayRequest"]["headers"]

    assert headers["x-data-agent-grants"] == "data:read"
    assert headers["x-data-agent-signature"] != "forged"
    assert headers["x-data-agent-issued-at"]
    assert "authorization" not in {key.lower() for key in headers}
    assert "data:sql:read" not in headers["x-data-agent-grants"]
