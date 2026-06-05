import json
import sys
from pathlib import Path
from typing import Any

import yaml


def _contains_placeholder(value: Any) -> bool:
    """Return whether a nested parameter value still contains a deployment marker."""

    if isinstance(value, str):
        return "REPLACE" in value
    if isinstance(value, list):
        return any(_contains_placeholder(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_placeholder(item) for item in value.values())
    return False


def _deployment_parameters(parameters: dict[str, Any], instance: str) -> dict[str, Any]:
    """Return top-level deployment parameters overlaid with per-agent overrides."""

    agent_overrides = parameters.get("agents", {}).get(instance, {})
    if not isinstance(agent_overrides, dict):
        raise SystemExit(f"agents.{instance} must be an object")
    return {**parameters, **agent_overrides}


def _placeholder_keys(parameters: dict[str, Any], instance: str) -> list[str]:
    """Find placeholders relevant to this deployment instance."""

    overridable = {
        "database_secret_arn",
        "openai_secret_arn",
        "oauth_default_return_url",
        "oauth_grant_type",
        "oauth_provider_arn",
        "oauth_scopes",
        "allowed_request_headers",
        "private_subnet_ids",
        "runtime_security_group_ids",
        "target_credential_provider_type",
    }
    visible_parameters = _deployment_parameters(parameters, instance)
    invalid: list[str] = []
    for key, value in visible_parameters.items():
        if key == "agents":
            continue
        if key in overridable and key in parameters.get("agents", {}).get(instance, {}):
            value = parameters["agents"][instance][key]
        if _contains_placeholder(value):
            invalid.append(key)
    return sorted(invalid)


def _validate_authorization_config(authorization: dict[str, Any]) -> None:
    """Validate that authorization mode and accepted claims cannot drift apart."""

    accepted_claims = set(authorization.get("accepted_claims", []))
    mode = authorization.get("mode")
    if mode not in {"scopes", "claims"}:
        raise SystemExit("authorization.mode must be either scopes or claims")
    unsupported_claims = accepted_claims - {"scope", "scp", "roles"}
    if unsupported_claims:
        raise SystemExit(
            f"Unsupported authorization claims: {', '.join(sorted(unsupported_claims))}"
        )
    if not accepted_claims:
        raise SystemExit("At least one authorization claim must be configured")
    if mode == "scopes":
        invalid_claims = accepted_claims - {"scope", "scp"}
        if invalid_claims:
            raise SystemExit(
                "authorization.mode scopes accepts only scope or scp claims; "
                f"remove: {', '.join(sorted(invalid_claims))}"
            )
        if accepted_claims.isdisjoint({"scope", "scp"}):
            raise SystemExit(
                "authorization.mode scopes requires scope or scp in accepted_claims"
            )
    if mode == "claims" and accepted_claims != {"roles"}:
        raise SystemExit(
            "authorization.mode claims currently supports only roles in accepted_claims"
        )


def _validate_target_credential_config(parameters: dict[str, Any]) -> None:
    """Validate the database agent target credential contract used by deploy.sh."""

    provider_type = parameters.get("target_credential_provider_type", "GATEWAY_IAM_ROLE")
    if provider_type != "GATEWAY_IAM_ROLE":
        raise SystemExit(
            "deploy.sh supports only GATEWAY_IAM_ROLE database targets; deploy OAUTH/OBO "
            "targets with infrastructure/target-mcp-oauth-obo.yaml or dedicated automation"
        )
    oauth_parameters = {
        "oauth_default_return_url",
        "oauth_grant_type",
        "oauth_provider_arn",
        "oauth_scopes",
    }
    configured = sorted(key for key in oauth_parameters if parameters.get(key))
    if configured:
        raise SystemExit(
            "OAUTH target parameters are not used by deploy.sh for database targets: "
            + ", ".join(configured)
        )


def _validate_allowed_request_headers(parameters: dict[str, Any]) -> None:
    """Ensure the GatewayTarget forwards every signed header the Runtime requires."""

    configured = parameters.get("allowed_request_headers")
    if not configured:
        return
    headers = {
        header.strip().lower()
        for header in str(configured).split(",")
        if header.strip()
    }
    required = {
        "x-data-agent-grants",
        "x-data-agent-identity",
        "x-data-agent-issued-at",
        "x-data-agent-signature",
    }
    missing = sorted(required - headers)
    if missing:
        raise SystemExit(
            "allowed_request_headers must include signed Gateway headers: "
            + ", ".join(missing)
        )


def main() -> None:
    path = Path(sys.argv[1])
    environment = sys.argv[2]
    config_path = Path(sys.argv[3])
    instance = sys.argv[4] if len(sys.argv) > 4 else "data-agent"
    with path.open(encoding="utf-8") as handle:
        raw_parameters = json.load(handle)
    parameters = _deployment_parameters(raw_parameters, instance)
    with config_path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    invalid = _placeholder_keys(raw_parameters, instance)
    if invalid:
        raise SystemExit(
            f"Deployment parameters still contain REPLACE markers: {', '.join(invalid)}"
        )
    _validate_target_credential_config(parameters)
    _validate_allowed_request_headers(parameters)

    required_scope = parameters.get("required_scope")
    authorization = config.get("authorization", {})
    configured_scope = authorization.get("required_scope")
    if required_scope != configured_scope:
        raise SystemExit(
            "Gateway required_scope must match authorization.required_scope in configuration"
        )
    _validate_authorization_config(authorization)
    identity_claims = set(authorization.get("identity_claims", []))
    unsupported_identity_claims = identity_claims - {
        "sub",
        "oid",
        "preferred_username",
        "upn",
        "appid",
        "azp",
        "client_id",
        "tid",
    }
    if unsupported_identity_claims:
        raise SystemExit(
            "Unsupported identity claims: "
            f"{', '.join(sorted(unsupported_identity_claims))}"
        )
    capabilities = config.get("capabilities", [])
    for capability in capabilities:
        identity_mode = capability.get("identity_mode")
        if identity_mode not in {"service", "on_behalf_of_user"}:
            raise SystemExit(
                "capability identity_mode must be service or on_behalf_of_user"
            )
        if identity_mode == "on_behalf_of_user" and not capability.get(
            "downstream_audience"
        ):
            raise SystemExit(
                "on_behalf_of_user capabilities must declare downstream_audience"
            )
    ask_database = next(
        (
            capability
            for capability in capabilities
            if capability.get("name") == "ask_database"
        ),
        None,
    )
    if ask_database is not None:
        required_grants = set(ask_database.get("required_grants", []))
        if required_scope not in required_grants:
            raise SystemExit(
                "ask_database.required_grants must include authorization.required_scope"
            )

    secret_arn = parameters.get("database_secret_arn", "")
    expected_secret_path = f":secret:/data-agent/{environment}/"
    if expected_secret_path not in secret_arn:
        raise SystemExit(
            f"database_secret_arn must be under /data-agent/{environment}/"
        )

    region = parameters.get("region", "")
    provider = config.get("llm", {}).get("provider")
    if provider not in {"bedrock", "openai"}:
        raise SystemExit("llm.provider must be either bedrock or openai")
    model = config.get("llm", {}).get("bedrock_model_id") or config.get("llm", {}).get(
        "model", ""
    )
    if provider == "bedrock" and region.startswith("eu-") and model.startswith("us."):
        raise SystemExit("A US Bedrock inference profile cannot be used from an EU region")
    if provider == "bedrock" and region.startswith("us-") and model.startswith("eu."):
        raise SystemExit("An EU Bedrock inference profile cannot be used from a US region")

    if provider == "openai" and not parameters.get("openai_secret_arn"):
        raise SystemExit("openai_secret_arn is required when llm.provider is openai")

    query_timeout = config.get("query", {}).get("timeout_seconds", 0)
    statement_timeout_ms = config.get("database", {}).get("statement_timeout_ms", 0)
    required_query_timeout = statement_timeout_ms / 1000 + 5
    if query_timeout <= required_query_timeout:
        raise SystemExit(
            "query.timeout_seconds must be at least 5 seconds greater than "
            "database.statement_timeout_ms"
        )


if __name__ == "__main__":
    main()
