from scripts.validate_parameters import (
    _contains_placeholder,
    _deployment_parameters,
    _placeholder_keys,
    _validate_authorization_config,
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
