import base64
import hashlib
import hmac
import json
import time
from typing import Any

from pydantic import BaseModel, Field


class CallerIdentity(BaseModel):
    """Trusted caller identity claims propagated by the Gateway interceptor."""

    claims: dict[str, str] = Field(default_factory=dict)

    @property
    def subject(self) -> str | None:
        return self.claims.get("sub") or self.claims.get("oid")

    @property
    def display_name(self) -> str | None:
        return self.claims.get("preferred_username") or self.claims.get("upn")

    def audit_fields(self) -> dict[str, str]:
        fields: dict[str, str] = {}
        if self.subject:
            fields["caller_subject"] = self.subject
        if self.display_name:
            fields["caller_display_name"] = self.display_name
        for key in ("appid", "azp", "client_id", "tid"):
            if key in self.claims:
                fields[f"caller_{key}"] = self.claims[key]
        return fields


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


def identity_from_claims(
    claims: dict[str, Any], identity_claims: list[str]
) -> CallerIdentity:
    """Extract the small caller identity subset allowed for Runtime propagation."""

    selected: dict[str, str] = {}
    for claim in identity_claims:
        value = claims.get(claim)
        if isinstance(value, str) and value:
            selected[claim] = value
    return CallerIdentity(claims=selected)


def _base64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _base64url_decode(raw: str) -> bytes:
    padded = raw + "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(padded)


def issue_internal_context_jwt(
    secret: str,
    *,
    issuer: str,
    audience: str,
    grants: list[str],
    identity: CallerIdentity,
    ttl_seconds: int,
    now: float | None = None,
) -> str:
    """Issue the short-lived internal authorization context token."""

    issued_at = int(now if now is not None else time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "ver": "1",
        "iss": issuer,
        "aud": audience,
        "grants": sorted(set(grants)),
        "identity": identity.claims,
        "iat": issued_at,
        "exp": issued_at + ttl_seconds,
    }
    encoded_header = _base64url_encode(
        json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
    )
    encoded_payload = _base64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    )
    signing_input = f"{encoded_header}.{encoded_payload}".encode()
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_payload}.{_base64url_encode(signature)}"


def verify_internal_context_jwt(
    secret: str,
    token: str,
    *,
    issuer: str,
    audience: str,
    leeway_seconds: int = 30,
    now: float | None = None,
) -> tuple[set[str], CallerIdentity]:
    """Verify the internal context JWT and return grants plus caller identity."""

    if not secret or not token:
        raise PermissionError("missing_internal_context")
    try:
        encoded_header, encoded_payload, encoded_signature = token.split(".")
    except ValueError:
        raise PermissionError("invalid_internal_context") from None
    signing_input = f"{encoded_header}.{encoded_payload}".encode()
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    try:
        provided = _base64url_decode(encoded_signature)
    except Exception:
        raise PermissionError("invalid_internal_context") from None
    if not hmac.compare_digest(expected, provided):
        raise PermissionError("invalid_internal_context")
    try:
        header = json.loads(_base64url_decode(encoded_header))
        payload = json.loads(_base64url_decode(encoded_payload))
    except Exception:
        raise PermissionError("invalid_internal_context") from None
    if header.get("alg") != "HS256" or header.get("typ") != "JWT":
        raise PermissionError("invalid_internal_context")
    if payload.get("iss") != issuer or payload.get("aud") != audience:
        raise PermissionError("invalid_internal_context")
    current = int(now if now is not None else time.time())
    issued_at = payload.get("iat")
    expires_at = payload.get("exp")
    if not isinstance(issued_at, int) or not isinstance(expires_at, int):
        raise PermissionError("invalid_internal_context")
    if issued_at > current + leeway_seconds:
        raise PermissionError("invalid_internal_context")
    if expires_at < current - leeway_seconds:
        raise PermissionError("expired_internal_context")
    identity_claims = payload.get("identity", {})
    if not isinstance(identity_claims, dict):
        raise PermissionError("invalid_internal_context")
    return set(claim_values(payload.get("grants"))), CallerIdentity(
        claims={
            str(key): str(value)
            for key, value in identity_claims.items()
            if value is not None
        }
    )
