from scripts.validate_parameters import (
    _contains_placeholder,
    _deployment_parameters,
    _placeholder_keys,
    _validate_allowed_request_headers,
    _validate_authorization_config,
    _validate_target_credential_config,
)


def test_detects_nested_deployment_marker() -> None:
    assert _contains_placeholder({"value": ["ready", "REPLACE-value"]})


def test_accepts_completed_parameters() -> None:
    assert not _contains_placeholder({"value": ["ready", "configured"]})


def test_agent_overrides_top_level_parameters() -> None:
    parameters = {
        "database_secret_arn": "REPLACE",
        "agents": {"cmdb": {"database_secret_arn": "secret"}},
    }

    merged = _deployment_parameters(parameters, "cmdb")

    assert merged["database_secret_arn"] == "secret"


def test_placeholder_check_ignores_other_agent_overrides() -> None:
    parameters = {
        "region": "eu-west-1",
        "database_secret_arn": "top-level-secret",
        "agents": {
            "cmdb": {"database_secret_arn": "cmdb-secret"},
            "assets": {"database_secret_arn": "REPLACE-assets"},
        },
    }

    assert _placeholder_keys(parameters, "cmdb") == []


def test_rejects_oauth_target_for_database_deploy() -> None:
    try:
        _validate_target_credential_config(
            {
                "target_credential_provider_type": "OAUTH",
                "oauth_provider_arn": "arn:aws:bedrock-agentcore:eu-west-1:111122223333:token-vault/default/oauth2credentialprovider/docs",
                "oauth_scopes": "docs.read",
                "oauth_grant_type": "TOKEN_EXCHANGE",
            }
        )
    except SystemExit as exc:
        assert "supports only GATEWAY_IAM_ROLE" in str(exc)
        return
    raise AssertionError("Expected SystemExit")


def test_rejects_oauth_parameters_for_database_deploy() -> None:
    try:
        _validate_target_credential_config(
            {
                "target_credential_provider_type": "GATEWAY_IAM_ROLE",
                "oauth_provider_arn": "arn:aws:bedrock-agentcore:eu-west-1:111122223333:token-vault/default/oauth2credentialprovider/docs",
            }
        )
    except SystemExit as exc:
        assert "OAUTH target parameters are not used" in str(exc)
        return
    raise AssertionError("Expected SystemExit")


def test_accepts_gateway_iam_target_config() -> None:
    _validate_target_credential_config(
        {
            "target_credential_provider_type": "GATEWAY_IAM_ROLE",
        }
    )


def test_rejects_allowed_headers_without_signature_headers() -> None:
    try:
        _validate_allowed_request_headers(
            {"allowed_request_headers": "x-data-agent-grants,x-data-agent-identity"}
        )
    except SystemExit as exc:
        assert "x-data-agent-signature" in str(exc)
        return
    raise AssertionError("Expected SystemExit")


def test_accepts_allowed_headers_with_signature_headers() -> None:
    _validate_allowed_request_headers(
        {
            "allowed_request_headers": (
                "x-data-agent-grants,x-data-agent-identity,"
                "x-data-agent-issued-at,x-data-agent-signature"
            )
        }
    )


def test_scopes_mode_rejects_roles_claim() -> None:
    try:
        _validate_authorization_config(
            {"mode": "scopes", "accepted_claims": ["scope", "scp", "roles"]}
        )
    except SystemExit as exc:
        assert "scopes accepts only scope or scp" in str(exc)
        return
    raise AssertionError("Expected SystemExit")


def test_claims_mode_rejects_scope_claims() -> None:
    try:
        _validate_authorization_config(
            {"mode": "claims", "accepted_claims": ["roles", "scp"]}
        )
    except SystemExit as exc:
        assert "claims currently supports only roles" in str(exc)
        return
    raise AssertionError("Expected SystemExit")


def test_accepts_clean_scopes_mode() -> None:
    _validate_authorization_config({"mode": "scopes", "accepted_claims": ["scope", "scp"]})


def test_accepts_clean_claims_mode() -> None:
    _validate_authorization_config({"mode": "claims", "accepted_claims": ["roles"]})
