from app.authorization import (
    CallerIdentity,
    grants_from_claims,
    identity_from_claims,
    issue_internal_context_jwt,
    verify_internal_context_jwt,
)


def test_extracts_delegated_entra_scopes_from_scp() -> None:
    grants = grants_from_claims({"scp": "data:read data:sql:read"}, ["scp", "roles"])

    assert grants == {"data:read", "data:sql:read"}


def test_extracts_entra_application_roles() -> None:
    grants = grants_from_claims({"roles": ["data:read"]}, ["scp", "roles"])

    assert grants == {"data:read"}


def test_extracts_generic_scope_claim() -> None:
    grants = grants_from_claims({"scope": "data:read"}, ["scope"])

    assert grants == {"data:read"}


def test_extracts_bounded_identity_claims() -> None:
    identity = identity_from_claims(
        {
            "sub": "user-1",
            "preferred_username": "ana@example.com",
            "email": "ana@example.com",
        },
        ["sub", "preferred_username"],
    )

    assert identity.claims == {
        "sub": "user-1",
        "preferred_username": "ana@example.com",
    }


def test_verifies_internal_context_jwt() -> None:
    token = issue_internal_context_jwt(
        "secret",
        issuer="data-agent-gateway",
        audience="runtime:cmdb",
        grants=["data:read"],
        identity=CallerIdentity(claims={"sub": "user-1"}),
        ttl_seconds=300,
        now=1000,
    )

    grants, identity = verify_internal_context_jwt(
        "secret",
        token,
        issuer="data-agent-gateway",
        audience="runtime:cmdb",
        now=1100,
    )

    assert grants == {"data:read"}
    assert identity.subject == "user-1"


def test_rejects_internal_context_wrong_audience() -> None:
    token = issue_internal_context_jwt(
        "secret",
        issuer="data-agent-gateway",
        audience="runtime:cmdb",
        grants=["data:read"],
        identity=CallerIdentity(claims={"sub": "user-1"}),
        ttl_seconds=300,
        now=1000,
    )

    try:
        verify_internal_context_jwt(
            "secret",
            token,
            issuer="data-agent-gateway",
            audience="runtime:assets",
            now=1100,
        )
    except PermissionError as exc:
        assert str(exc) == "invalid_internal_context"
        return
    raise AssertionError("Expected PermissionError")


def test_rejects_expired_internal_context_jwt() -> None:
    token = issue_internal_context_jwt(
        "secret",
        issuer="data-agent-gateway",
        audience="runtime:cmdb",
        grants=["data:read"],
        identity=CallerIdentity(claims={"sub": "user-1"}),
        ttl_seconds=300,
        now=1000,
    )

    try:
        verify_internal_context_jwt(
            "secret",
            token,
            issuer="data-agent-gateway",
            audience="runtime:cmdb",
            now=1400,
        )
    except PermissionError as exc:
        assert str(exc) == "expired_internal_context"
        return
    raise AssertionError("Expected PermissionError")


def test_rejects_tampered_internal_context_jwt() -> None:
    token = issue_internal_context_jwt(
        "secret",
        issuer="data-agent-gateway",
        audience="runtime:cmdb",
        grants=["data:read"],
        identity=CallerIdentity(claims={"sub": "user-1"}),
        ttl_seconds=300,
        now=1000,
    )

    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")

    try:
        verify_internal_context_jwt(
            "secret",
            tampered,
            issuer="data-agent-gateway",
            audience="runtime:cmdb",
            now=1100,
        )
    except PermissionError as exc:
        assert str(exc) == "invalid_internal_context"
        return
    raise AssertionError("Expected PermissionError")
