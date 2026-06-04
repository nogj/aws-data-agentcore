from typing import Any


def claim_values(raw: Any) -> list[str]:
    """Normalize JWT claim values used by common OIDC providers."""

    if raw is None:
        return []
    if isinstance(raw, str):
        return raw.split()
    return [str(item) for item in raw]


def grants_from_claims(claims: dict[str, Any], accepted_claims: list[str]) -> set[str]:
    """Extract delegated scopes and app roles from configured JWT claim names."""

    grants: set[str] = set()
    for claim in accepted_claims:
        grants.update(claim_values(claims.get(claim)))
    return grants
