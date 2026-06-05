# Read-Only Data Agent MCP for Amazon Bedrock AgentCore

Production package for deploying a modular AgentCore tool hub, with the first
implemented module being a reusable read-only database data agent.

AgentCore Gateway is intended to act as the hub for multiple tool targets with
different authorization and downstream identity models. The first Runtime target
exposes one public tool, `ask_database`. SQLAlchemy Core provides the database
execution abstraction without an internal subprocess. The database domain,
authorized relations, glossary, prompts, and business terminology live only in
`config/data-agent.yaml`.

## Contents

```text
aws-data-agentcore/
├── app/                     Shared runtime code
│   └── capabilities/        Tool modules behind the Gateway hub
│       └── database/        Read-only database capability
├── config/                  Versioned non-sensitive configuration for S3
├── docs/                    Architecture and design notes
├── infrastructure/          CloudFormation for bootstrap, Runtime, and target
├── postgres/                Generic read-only PostgreSQL permission templates
├── scripts/                 Build, publication, and deployment scripts
└── tests/                   Unit tests for critical controls
```

See [docs/security_architecture.md](docs/security_architecture.md) for the
detailed security architecture.

## Prerequisites

- AWS CLI authenticated with permissions for CloudFormation, S3, IAM, Secrets
  Manager, Bedrock, AgentCore, and CloudWatch Logs.
- Python 3.13 and `zip`.
- A private VPC route to the target database.
- NAT or equivalent outbound connectivity when using OpenAI.
- VPC endpoints for S3, Secrets Manager, and Bedrock are recommended.
- An OIDC/JWT provider for authenticating Gateway consumers.
- Database-specific authorized views and a read-only technical role.

## Design

- Direct Python ZIP deployment from S3, without containers.
- Linux arm64 dependencies packaged in an artifact below the 250 MB limit.
- Secrets stored in Secrets Manager; S3 contains only artifacts and
  non-sensitive configuration.
- Stateless and cache-free operation.
- Bedrock as the default provider. OpenAI remains selectable only after an
  explicit data-governance decision.
- Business answers, assumptions, and warnings use the language of the question.
- Rejection and operational error messages are fixed configuration values and
  never invoke an LLM.
- All LLM prompts are loaded from versioned configuration.
- Security does not depend on the LLM. For the database module, SQLGlot
  validates SQL and the database enforces read-only permissions again. Future
  modules must define equivalent deterministic guardrails for their own domain.
- Database-specific transaction controls are implemented as explicit adapters.
  PostgreSQL is the first supported adapter.
- Gateway validates the required JWT scope and a managed request interceptor
  propagates validated grants and bounded caller identity to target runtimes.
  Each target still fails closed when trusted authorization context is absent.
- Gateway targets are managed by CloudFormation for rollback, drift detection,
  and clean deletion. The database target stack is deployed after the Runtime so
  the MCP endpoint can contain the URL-encoded Runtime ARN required by
  AgentCore.

## Preparation

1. Configure the database specialization in `config/data-agent.yaml`.
2. Create database-specific security-barrier views that expose only approved
   columns and relationships.
3. Apply the generic PostgreSQL templates in `postgres/` using your approved
   database, schema, role names, and the concrete authorized view list.
4. Create the database secret. For a single `data-agent` deployment, one secret
   is enough:

```bash
aws secretsmanager create-secret \
  --name /data-agent/prod/database \
  --secret-string '{"database_uri":"postgresql+psycopg://ROLE:REPLACE@db.internal:5432/DATABASE?sslmode=verify-full"}'

```

For multiple database agents, create one database secret per instance so each
Runtime receives only its own connection string and read-only role:

```bash
aws secretsmanager create-secret \
  --name /data-agent/prod/cmdb \
  --secret-string '{"database_uri":"postgresql+psycopg://CMDB_ROLE:REPLACE@cmdb.internal:5432/CMDB?sslmode=verify-full"}'

aws secretsmanager create-secret \
  --name /data-agent/prod/assets \
  --secret-string '{"database_uri":"postgresql+psycopg://ASSETS_ROLE:REPLACE@assets.internal:5432/ASSETS?sslmode=verify-full"}'
```

Reference those ARNs under `agents.<instance>.database_secret_arn` in the
parameter file. Secret names must stay under `/data-agent/<environment>/` so the
Runtime IAM policy and deployment validation remain aligned.

5. Complete `infrastructure/parameters.json` or create an equivalent file per
   environment. Deployment scripts use `parameters.<environment>.json` when it
   exists and otherwise fall back to `parameters.json`. They stop before
   deployment if any `REPLACE` marker remains or if the scope, secret path, or
   Bedrock model geography is inconsistent.
   The committed `parameters.json` is a template and is not expected to deploy
   unchanged.

6. Only when OpenAI has been approved for the configured data classification,
   create `/data-agent/<environment>/openai`, set `llm.provider: openai`, and
   add `openai_secret_arn` to the parameter file. `deploy.sh` passes it to the
   Runtime stack.

## Build And Deployment

```bash
./scripts/bootstrap.sh prod
./scripts/build.sh
./scripts/publish.sh prod
export ARTIFACT_KEY=artifacts/prod/data-agent-REPLACE.zip
export CONFIG_KEY=config/prod/data-agent-REPLACE.yaml
./scripts/deploy.sh prod
./scripts/smoke_test.sh prod
```

`build.sh` uses `pip --platform manylinux2014_aarch64` to create a compatible
AgentCore Runtime ZIP. Run it in CI and retain the checksum generated in
`dist/`.

`publish.sh` prints the exact `ARTIFACT_KEY` and `CONFIG_KEY` values required by
`deploy.sh`. It also publishes a versioned manifest and
`manifests/<environment>/active.json` containing the artifact SHA-256.

`bootstrap.sh` runs an AWS preflight that confirms the configured subnets and
security groups exist and belong to one VPC, the database secret exists, and
the Bedrock inference profile can be resolved from the configured Region. A
successful Runtime deployment and smoke test are still required to prove
AgentCore AZ support, service endpoint access, and database connectivity.

`smoke_test.sh` performs both MCP discovery and an actual `tools/call` to
`ask_database`. Override the safe question with `SMOKE_QUESTION` and the row
bound with `SMOKE_MAX_ROWS`.

## Multiple Database Agents

The Gateway is shared. Each database agent should be deployed as a separate
Runtime and GatewayTarget with its own config file, database secret, target
name, Runtime IAM role, grants, and authorized data model. The default
`data-agent` instance keeps the legacy stack names and S3 key layout.

For a second database agent:

```bash
./scripts/build.sh
DATA_AGENT_INSTANCE=cmdb CONFIG_FILE=config/cmdb-agent.yaml ./scripts/publish.sh prod
export ARTIFACT_KEY=artifacts/prod/cmdb/data-agent-REPLACE.zip
export CONFIG_KEY=config/prod/cmdb/data-agent-REPLACE.yaml
DATA_AGENT_INSTANCE=cmdb CONFIG_FILE=config/cmdb-agent.yaml ./scripts/deploy.sh prod
DATA_AGENT_INSTANCE=cmdb CONFIG_FILE=config/cmdb-agent.yaml ./scripts/smoke_test.sh prod
```

`DATA_AGENT_INSTANCE` drives the Runtime stack suffix, target name, manifest
prefix, per-instance Runtime IAM role name, and smoke-test target selection.
Override `TARGET_NAME` only if the Gateway target name should differ from the
instance name. `deploy.sh` lets `infrastructure/runtime.yaml` create the
Runtime IAM role by default; pass `RUNTIME_ROLE_ARN` only for a deliberate
legacy/shared-role override.

Prompts are also per instance. Each Runtime receives its own `CONFIG_KEY`, so
the `prompts.sql_generation` and `prompts.result_summary` sections in
`CONFIG_FILE` are scoped to that database agent. Use separate config files when
different databases need different prompt wording, glossary, synonyms, SQL
rules, row limits, or authorized data models.

Per-agent infrastructure settings can live under `agents` in the parameter
file. Agent-specific values override the top-level defaults:

```json
{
  "region": "eu-west-1",
  "artifact_bucket_name": "corp-data-agent-artifacts",
  "jwt_discovery_url": "https://login.example/.well-known/openid-configuration",
  "jwt_allowed_audience": "api://data-agent",
  "required_scope": "data:read",
  "agents": {
    "cmdb": {
      "database_secret_arn": "arn:aws:secretsmanager:eu-west-1:111122223333:secret:/data-agent/prod/cmdb",
      "private_subnet_ids": "subnet-a,subnet-b",
      "runtime_security_group_ids": "sg-cmdb"
    },
    "assets": {
      "database_secret_arn": "arn:aws:secretsmanager:eu-west-1:111122223333:secret:/data-agent/prod/assets",
      "private_subnet_ids": "subnet-a,subnet-b",
      "runtime_security_group_ids": "sg-assets"
    }
  }
}
```

Checklist for adding a new database agent:

- Create database-specific security-barrier views that expose only approved
  relations and columns.
- Create a dedicated read-only database role for that agent and grant it only
  the approved views.
- Create a Secrets Manager secret under `/data-agent/<environment>/<instance>`
  containing that role's connection string.
- Create a dedicated config YAML with the instance's prompts, data model,
  glossary, synonyms, SQL rules, query limits, and capability grants.
- Add `agents.<instance>` overrides to the parameter file for the database
  secret, subnets, and security groups when they differ from the defaults.
- Run `build.sh` once for the code artifact.
- Run `publish.sh` with `DATA_AGENT_INSTANCE=<instance>` and
  `CONFIG_FILE=<path-to-config>`.
- Export the printed `ARTIFACT_KEY` and `CONFIG_KEY`, then run `deploy.sh` with
  the same `DATA_AGENT_INSTANCE` and `CONFIG_FILE`.
- Run `smoke_test.sh` for the instance and verify the Gateway lists/calls the
  intended target.
- Review audit logs, database query logs, and IAM/secret access before broad
  access is granted.

Versioned artifacts and configuration keys are published immutably. Use
`scripts/cleanup_artifacts.py` to remove keys that are not referenced by the
active manifest, the retained manifest window, or the currently deployed
Runtime stack parameters. It runs as a dry run unless `--apply` is passed.

## Configuration

The runtime receives only bootstrap references:

- `CONFIG_BUCKET`
- `CONFIG_KEY`
- `DATABASE_SECRET_ARN`
- `OPENAI_SECRET_ARN` when OpenAI is enabled
- `APP_ENV`
- `AWS_REGION`

The `database` section selects the SQLAlchemy execution adapter, SQLGlot
dialect, connection arguments, and statement timeout. The `data_model` section
defines the database-specific authorized relations, columns, descriptions,
glossary, synonyms, SQL rules, and allowed SQL functions. Functions fail closed:
any function not listed in `allowed_functions` is rejected. The `prompts`
section contains SQL generation and result summarization instructions.

Optional `context` input is bounded by item count and key/value length before it
can be included in a prompt. SQL columns are validated against their configured
relation rather than a global union of authorized columns.

`query.timeout_seconds` bounds the complete request path after basic validation:
SQL generation, database execution, and result summarization. The database has
its own `statement_timeout_ms`, and each LLM provider receives
`llm.timeout_seconds` where supported. Keep the global query timeout greater
than the database statement timeout plus operational margin, because cancelling
the async request does not forcibly stop a synchronous DB thread before the
database-side timeout fires.

Supporting another database requires its SQLAlchemy driver, a matching SQLGlot
dialect, and an adapter in `app/capabilities/database/database.py` that applies
equivalent read-only and timeout controls. Unsupported dialects fail closed.

These sections can change without rebuilding the ZIP as long as their expected
contract remains stable.

Gateway validates inbound JWTs, including the configured `required_scope`.
The deployed request interceptor derives the `x-data-agent-grants` header from
configured JWT claims and replaces any value supplied by a consumer. It also
emits a bounded `x-data-agent-identity` header containing only the configured
identity claims. By default, in `scopes` mode, it accepts only `scope` and `scp`
as grants and propagates `sub`, `oid`, `preferred_username`, `appid`, `azp`, and
`tid` for audit. `roles` is reserved for `claims` mode. The Runtime also
requires the configured grant, so missing propagation denies access.

`authorization.mode` controls where the required grant is enforced first:

- `scopes`: Gateway also sets `AllowedScopes`. Use this for delegated OAuth
  scopes such as Entra `scp`. Accepted claims must be limited to `scope` and
  `scp`.
- `claims`: Gateway validates issuer and audience only; the managed interceptor
  and Runtime enforce `required_scope` from configured claims such as Entra
  `roles`. Use this for client-credentials flows with application roles.
  Accepted claims must be `roles`.

## Microsoft Entra ID

Use Entra ID as the OIDC provider for the AgentCore Gateway:

```json
{
  "jwt_discovery_url": "https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration",
  "jwt_allowed_audience": "api://<application-client-id>",
  "required_scope": "data:read"
}
```

Recommended Entra setup:

- Create an App Registration for the API exposed by this agent.
- Set the Application ID URI used as `jwt_allowed_audience`.
- For delegated user flows, expose scopes such as `data:read` and
  `data:sql:read`; Entra emits these in the `scp` claim.
- For client-credentials flows, define app roles with the same values; Entra
  emits these in the `roles` claim.
- Grant and consent the client applications that will call the Gateway.

Gateway `AllowedScopes` validates delegated scopes. Application roles are
enforced by the managed interceptor and the Runtime using the configured
`authorization.accepted_claims`. Keep `required_scope` aligned with either the
delegated scope value or the app role value you assign in Entra.

For delegated user flow, keep:

```yaml
authorization:
  mode: scopes
  required_scope: data:read
  accepted_claims: [scp, scope]
```

For client credentials with app roles, use:

```yaml
authorization:
  mode: claims
  required_scope: data:read
  accepted_claims: [roles]
```

Changing `authorization.mode` or `accepted_claims` affects the Gateway
interceptor environment and requires redeploying the bootstrap stack, not only
publishing a new S3 configuration file.

## Hub Capability Model

The `capabilities` section declares the authorization and downstream identity
mode expected by each exposed tool or Gateway target. The Gateway is the hub;
each target is a module with its own grants, identity mode, allowed headers,
audit contract, and domain guardrails.

```yaml
capabilities:
  - name: ask_database
    target: data-agent
    identity_mode: service
    required_grants: [data:read]
    sql_viewer_grant: data:sql:read
```

`identity_mode: service` means the target uses its own technical identity for
downstream access. `ask_database` uses this mode: Gateway authorizes the caller,
but PostgreSQL sees the fixed read-only database role from Secrets Manager.

Use `identity_mode: on_behalf_of_user` for future capabilities that must access
downstream systems with the caller's delegated authority, such as SharePoint,
Jira, Salesforce, or an internal API with user-level authorization. Such
capabilities must declare a `downstream_audience` and should use a dedicated
AgentCore Identity resource credential provider for token exchange or an
equivalent approved OBO pattern. Do not pass raw bearer tokens to every target
as a generic convenience; only the target that performs OBO should receive the
minimum token/context required for that exchange.

AgentCore Identity is treated as a common hub capability for targets that need
OBO, not as a mandatory dependency for every target. The database agent remains
`identity_mode: service` and does not use AgentCore Identity. Keep OBO credential
providers target-scoped; `config/identity-providers.example.json` shows the
expected inventory shape for future delegated-access targets.

Recommended grant naming for additional targets:

- `cmdb:read` for fixed-identity read-only data access.
- `data:sql:read` for generated SQL visibility.
- `kb:query` for querying an approved knowledge base.
- `docs:read` for user-delegated document search.
- `tickets:create` for user-delegated ticket creation.

Each target should receive only the headers it needs. The current Runtime target
allowlists `x-data-agent-grants` and `x-data-agent-identity`; future OBO targets
should have their own GatewayTarget metadata and credential-provider
configuration rather than broadening this target's contract.
`infrastructure/target.yaml` is parameterized with
`TargetName`, `TargetDescription`, `AllowedRequestHeaders`, and outbound
credential-provider settings so the same template can be reused for additional
Gateway targets with narrower header contracts.

For OBO targets, keep the capability declaration and the target credential
provider aligned:

```yaml
capabilities:
  - name: search_user_documents
    target: user-documents
    identity_mode: on_behalf_of_user
    required_grants: [docs:read]
    downstream_audience: api://sharepoint-or-internal-docs
    credential_provider_name: entra-docs-obo
```

```json
{
  "agents": {
    "user-documents": {
      "target_credential_provider_type": "OAUTH",
      "oauth_provider_arn": "arn:aws:bedrock-agentcore:eu-west-1:111122223333:token-vault/default/oauth2credentialprovider/entra-docs-obo",
      "oauth_scopes": "https://graph.microsoft.com/.default",
      "oauth_grant_type": "AUTHORIZATION_CODE",
      "allowed_request_headers": "x-data-agent-grants,x-data-agent-identity"
    }
  }
}
```

`GATEWAY_IAM_ROLE` remains the default for AgentCore-hosted MCP Runtime
targets, including the database agent. Use `OAUTH` only for targets that need
AgentCore Identity/outbound authorization or an equivalent approved OBO
credential provider. When a target needs more than one OAuth scope, provide
`oauth_scopes` as a comma-separated value because the CloudFormation parameter
is a `CommaDelimitedList`.

## Data Governance

Query results are sent to the selected LLM to produce the natural-language
answer. Bedrock is the default, but its use still requires review of data
classification, regional processing, logging, retention, and model access.
Enabling OpenAI additionally requires explicit approval for external-provider
processing, data residency, contractual terms, and permitted fields.

## Operational Boundaries

`LIMIT` restricts returned rows, not the amount of work performed by a query.
Production database preparation must include narrow security-barrier views,
appropriate indexes, tested statement timeouts, the technical role connection
limit, query monitoring, and preferably a read replica for analytical traffic.

Gateway authentication and scope validation do not provide consumer quotas or
cost budgets. Deploy rate limiting, per-consumer quotas, anomaly detection, and
cost alarms in the approved ingress and monitoring architecture before broad
access is granted.

The artifact bucket never expires current objects automatically, because the
Runtime may still reference a versioned artifact or configuration key.
Noncurrent versions of overwritten keys expire after the configured retention
period. Run the manifest-aware cleanup script for unreferenced versioned keys. The
scope interceptor log group has explicit retention. Confirm and configure
retention for AgentCore-managed Runtime and Gateway logs according to the
organization logging standard.

## Production Checklist

- Review every configured relation and denied column.
- Validate TLS, timeouts, and private connectivity against the target database.
- Confirm every authorized view has an explicit `SELECT` grant to the technical
  role and no broader relation access.
- Review the SQL function allowlist and database function execution privileges.
- Load test expensive joins and aggregations against representative data.
- Confirm the Runtime VPC has the required AWS service endpoints and outbound
  connectivity for the selected LLM provider.
- Ensure the principal creating the first VPC Runtime can create
  `AWSServiceRoleForBedrockAgentCoreNetwork`.
- Review rate limits, quotas, cost alarms, log retention, and audit requirements.
- Validate the treatment of data sent to any external LLM provider.
- Test rollback of both artifact and configuration versions.

## AWS References

- [Direct code deployment for Python](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html)
- [Deploy MCP servers in AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-mcp.html)
- [MCP server targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-MCPservers.html)
- [AWS::BedrockAgentCore::Runtime](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-runtime.html)
- [AWS::BedrockAgentCore::Gateway](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-gateway.html)
