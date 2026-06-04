from app.authorization import grants_from_claims


def test_extracts_delegated_entra_scopes_from_scp() -> None:
    grants = grants_from_claims({"scp": "data:read data:sql:read"}, ["scp", "roles"])

    assert grants == {"data:read", "data:sql:read"}


def test_extracts_entra_application_roles() -> None:
    grants = grants_from_claims({"roles": ["data:read"]}, ["scp", "roles"])

    assert grants == {"data:read"}


def test_extracts_generic_scope_claim() -> None:
    grants = grants_from_claims({"scope": "data:read"}, ["scope"])

    assert grants == {"data:read"}
