# Microsoft Entra ID Setup Guide

This guide configures Microsoft Entra ID as the OIDC issuer for the AgentCore
Gateway. It uses two app registrations:

- **Data Agent API**: represents the protected API exposed by the Gateway.
- **Data Agent Client**: represents a caller used by a UI, CLI, automation, or
  smoke test.

Use placeholders only. Do not commit tenant IDs, application IDs, object IDs, or
secret values from a real environment.

## Target Runtime Authorization Model

For delegated user access, the agent expects:

```yaml
authorization:
  mode: scopes
  required_scope: data:read
  sql_viewer_scope: data:sql:read
  accepted_claims:
    - scope
    - scp
```

The Gateway validates the inbound JWT and `data:read`. The request interceptor
and Runtime also enforce the propagated grant.

For application-only access, use app roles and `authorization.mode: claims`
instead. Keep delegated scopes and app roles separate during review.

## 1. Create The API App Registration

In Entra admin center:

1. Go to **Identity > Applications > App registrations > New registration**.
2. Name it `Data Agent API`.
3. Choose the supported account type for the environment.
4. Leave redirect URI empty for the API app.
5. Create the app.

Record:

- `<tenant-id>`
- `<api-app-id>`: Application/client ID.
- `<api-object-id>`: Object ID of the app registration.

## 2. Configure The API Identifier

In **Data Agent API > Expose an API**:

1. Set **Application ID URI** to:

```text
api://<api-app-id>
```

2. Save.

This URI is used when clients request scopes. It is not always the value that
appears in the token `aud` claim, so verify a real token later.

## 3. Configure Access Token Version

When using the Entra v2 discovery document, make the API app emit v2 access
tokens.

With Azure CLI:

```bash
az login \
  --tenant <tenant-id> \
  --use-device-code \
  --allow-no-subscriptions

az rest \
  --method PATCH \
  --uri https://graph.microsoft.com/v1.0/applications/<api-object-id> \
  --headers Content-Type=application/json \
  --body '{"api":{"requestedAccessTokenVersion":2}}'
```

Verify:

```bash
az ad app show \
  --id <api-app-id> \
  --query '{requestedAccessTokenVersion:api.requestedAccessTokenVersion}' \
  --output json
```

Expected:

```json
{
  "requestedAccessTokenVersion": 2
}
```

## 4. Add Delegated Scopes

In **Data Agent API > Expose an API > Add a scope**, create:

| Scope | Who can consent | Purpose |
| --- | --- | --- |
| `data:read` | Admins and users, or admins only for production | Invoke the data agent. |
| `data:sql:read` | Admins only recommended | Allow SQL disclosure in agent responses when requested. |

Recommended production posture:

- Make `data:read` available only to approved users/groups via enterprise app
  assignment when possible.
- Keep `data:sql:read` admin-consented and restricted.
- Do not use broad scopes such as `user_impersonation` for this agent.

## 5. Create The Client App Registration

Create a second app registration:

1. Go to **App registrations > New registration**.
2. Name it `Data Agent Client`.
3. Choose the supported account type.
4. Configure redirect URI only if the client flow needs it.

Recommended redirect URI choices:

- CLI/device-code smoke tests: no redirect URI required.
- Local interactive web test: `http://localhost:<port>/callback`.
- Single-page app: configure SPA redirect URI and apply normal SPA token
  handling controls.

Record:

- `<client-app-id>`
- `<client-object-id>`

## 6. Grant API Permissions To The Client

In **Data Agent Client > API permissions**:

1. Select **Add a permission**.
2. Select **My APIs**.
3. Choose **Data Agent API**.
4. Choose **Delegated permissions**.
5. Select `data:read`.
6. Select `data:sql:read` only for clients that should display generated SQL.
7. Grant admin consent if required by tenant policy.

If **My APIs** is empty, check:

- The API app exists in the same tenant.
- The API app has an Application ID URI.
- The scopes are enabled.
- You are viewing app registrations in the correct directory.

## 7. Optional Authorized Client Applications

In **Data Agent API > Expose an API > Authorized client applications**, add
`<client-app-id>` only when you want to pre-authorize the client and avoid user
consent prompts.

For production, prefer an explicit admin consent process and document which
clients are authorized.

## 8. Obtain A Delegated Token For Validation

For CLI smoke tests, request the API scope:

```bash
az login \
  --tenant <tenant-id> \
  --use-device-code \
  --allow-no-subscriptions \
  --scope api://<api-app-id>/data:read

az account get-access-token \
  --tenant <tenant-id> \
  --scope api://<api-app-id>/data:read \
  --query accessToken \
  --output tsv
```

If Entra returns `AADSTS65001`, the Azure CLI application has not yet received
consent for the API scope. Repeat `az login` with the `--scope` argument and
complete the consent prompt, or grant admin consent from the portal.

## 9. Verify Token Claims

Decode the token payload locally. Do not paste real tokens into logs or issue
trackers.

```bash
TOKEN="$(az account get-access-token \
  --tenant <tenant-id> \
  --scope api://<api-app-id>/data:read \
  --query accessToken \
  --output tsv)"

python3 - <<'PY'
import base64
import json
import os

token = os.environ["TOKEN"]
payload = token.split(".")[1]
payload += "=" * ((4 - len(payload) % 4) % 4)
claims = json.loads(base64.urlsafe_b64decode(payload))
for key in ["ver", "iss", "aud", "tid", "scp", "roles", "azp", "appid", "oid"]:
    if key in claims:
        print(f"{key}: {claims[key]}")
PY
```

Expected delegated token shape:

```text
ver: 2.0
iss: https://login.microsoftonline.com/<tenant-id>/v2.0
aud: <access-token-aud-claim>
scp: data:read
```

Set the deployment parameters from the verified token:

```json
{
  "jwt_discovery_url": "https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration",
  "jwt_allowed_audience": "<access-token-aud-claim>",
  "required_scope": "data:read"
}
```

Important: `jwt_allowed_audience` must match the token `aud` claim exactly.
With Entra v2 tokens, this is often the bare API application client ID even
though clients request `api://<api-app-id>/data:read`.

## 10. Run The Agent Smoke Test

```bash
export BEARER_TOKEN="$(az account get-access-token \
  --tenant <tenant-id> \
  --scope api://<api-app-id>/data:read \
  --query accessToken \
  --output tsv)"

DATA_AGENT_INSTANCE=<agent-instance> \
CONFIG_FILE=<path-to-agent-config.yaml> \
PARAMS_FILE=<path-to-parameters.json> \
SMOKE_QUESTION="List one visible CI name and id." \
./scripts/smoke_test.sh <environment>
```

The smoke test:

- uses MCP protocol version `2025-06-18`;
- initializes an MCP session explicitly;
- performs `tools/list`;
- performs `tools/call`;
- closes a Gateway-returned `Mcp-Session-Id` when one is present.

For Runtime microVM affinity, the Gateway request interceptor derives and
overwrites `Mcp-Session-Id` from verified identity claims before forwarding to
the Runtime. Callers do not need to generate this header.

## 11. Application-Only Alternative

For machine-to-machine callers, use app roles instead of delegated scopes:

1. In **Data Agent API > App roles**, create app roles such as `data:read`.
2. Assign the role to **Data Agent Client** or another service principal.
3. Use client credentials to obtain a token.
4. Configure the agent:

```yaml
authorization:
  mode: claims
  required_scope: data:read
  accepted_claims:
    - roles
```

The Gateway validates issuer and audience. The interceptor and Runtime enforce
the `roles` claim.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `403 Forbidden` from Gateway | `aud`, issuer, token version, or scope mismatch. | Decode the token and align `jwt_allowed_audience`, discovery URL, and `required_scope`. |
| Token has `ver: 1.0` | API app is not configured for v2 access tokens. | Set `api.requestedAccessTokenVersion` to `2`. |
| `My APIs` is empty | API app is not exposed or wrong directory is selected. | Set Application ID URI, add scopes, confirm tenant. |
| `AADSTS65001` | Consent missing for the requesting client. | Complete interactive login with `--scope` or grant admin consent. |
| Token has no `scp` | You used app-only flow or requested the wrong permission type. | Use delegated permission for `scopes` mode, or switch to `claims` mode with app roles. |
| Smoke test lists tools but fails on call | Runtime/network/database issue rather than Entra. | Check Runtime logs, VPC endpoints, database secret, and model access. |

## Review Checklist

- API app has v2 tokens enabled.
- API app exposes only required scopes.
- Client app has only necessary delegated permissions.
- Admin consent and enterprise app assignment are documented.
- A real token was decoded and `jwt_allowed_audience` matches its `aud`.
- `data:sql:read` is restricted.
- No real IDs, secrets, or tokens are committed to the repository.
