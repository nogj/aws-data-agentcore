from app.authorization import (
    CallerIdentity,
    encode_identity,
    grants_from_claims,
    identity_from_claims,
    identity_from_header,
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


def test_round_trips_identity_header() -> None:
    encoded = encode_identity(
        CallerIdentity(claims={"sub": "user-1", "azp": "client-1"})
    )

    assert identity_from_header(encoded).audit_fields() == {
        "caller_subject": "user-1",
        "caller_azp": "client-1",
    }
