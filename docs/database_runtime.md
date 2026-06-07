# Database Runtime

The database Runtime exposes one MCP tool, `ask_database`, for read-only
natural-language questions over an approved database model.

This document is database-specific. Gateway/OIDC/OBO conventions belong in
[Gateway hub](gateway_hub.md), and cross-cutting security rationale belongs in
[Security architecture](security_architecture.md).

## Runtime Contract

`infrastructure/runtime.yaml` creates one AgentCore Runtime instance and one
Runtime IAM role. The Runtime receives only bootstrap references:

- `APP_ENV`
- `AWS_REGION`
- `CONFIG_BUCKET`
- `CONFIG_KEY`
- `DATABASE_SECRET_ARN`
- `GATEWAY_HEADER_SIGNING_SECRET_ARN`
- `OPENAI_SECRET_ARN` when OpenAI is enabled

The Runtime loads non-sensitive configuration from S3 on invocation and loads
sensitive values from Secrets Manager. The IAM role is scoped to the configured
artifact object, config object, database secret, header-signing secret, optional
OpenAI secret, Bedrock model invocation, and AgentCore logs.

`infrastructure/private-endpoints.yaml` creates the private AWS service
endpoints required by the Runtime: S3, Secrets Manager, Bedrock Runtime, and
CloudWatch Logs. `deploy.sh` deploys this stack before the Runtime unless
`create_private_service_endpoints=false` is configured for an environment that
already manages equivalent endpoints.

Endpoint stacks are product-managed and shared by environment/VPC, not by
agent. The deploy script first searches for an existing
`data-agent-private-endpoints-<environment>*` stack whose `VpcId` parameter
matches the target VPC. If found, it reuses that stack and merges the Runtime
route tables so existing agents keep their S3 gateway access. If no matching
stack exists, it creates `data-agent-private-endpoints-<environment>-<vpc-id>`.
Use `private_service_endpoint_stack_name` only to force a specific
product-managed endpoint stack.

The endpoint stack also creates a shared Runtime access security group. The
endpoint security group allows HTTPS ingress from that shared SG, and
`deploy.sh` attaches the shared SG to each Runtime alongside the
agent-specific Runtime SGs. This keeps endpoint access reusable for future
agents without allowing the entire VPC CIDR by default.

`infrastructure/agent-foundation.yaml` is optional per agent. When
`runtime_network_mode=managed`, it creates Runtime-only private subnets, a
Runtime security group, and route-table outputs inside the configured VPC. When
`database_secret_mode=managed`, it creates the agent database secret under
`/data-agent/<environment>/<instance>/...`. The foundation stack creates the
secret container with an empty JSON object, then `deploy.sh` validates and
writes `database_secret_string` through Secrets Manager after the stack exists.
The database URI is therefore not passed as a CloudFormation parameter. If
either mode remains `external`, the deployment keeps using the caller-supplied
subnet, security group, or secret ARN.

## Source Layout

```text
app/
├── authorization.py          Shared signed-header and grant helpers
├── audit.py                  Shared structured audit helper
├── config.py                 Shared validated configuration model
└── capabilities/
    └── database/
        ├── database.py       SQLAlchemy execution and transaction controls
        ├── llm.py            SQL generation chain
        ├── models.py         Public tool and structured LLM models
        ├── security.py       Database input/output controls
        └── sql_validator.py  SQLGlot validation for generated SQL
```

Additional modules belong under `app/capabilities/<module>/`. Shared code moves
to top-level `app/` only when at least two modules genuinely need it.

## Tool Flow

```mermaid
sequenceDiagram
    participant Client
    participant Gateway
    participant Runtime as Database Runtime
    participant LLM
    participant DB as PostgreSQL

    Client->>Gateway: tools/call ask_database
    Gateway->>Runtime: MCP request with signed grants and identity headers
    Runtime->>Runtime: Verify Gateway header signature
    Runtime->>Runtime: Enforce required_grants
    Runtime->>Runtime: Validate question and context bounds
    Runtime->>LLM: Generate structured SQL candidate
    LLM-->>Runtime: SQL and assumptions
    Runtime->>Runtime: Validate SQL with SQLGlot
    Runtime->>DB: Execute validated SELECT in read-only transaction
    DB-->>Runtime: Rows
    Runtime->>Runtime: Normalize, redact, and bound rows
    Runtime-->>Client: Canonical data object, optional SQL when authorized
```

The LLM is used to generate a SQL candidate. Fixed Runtime code validates the
candidate and builds the canonical `data` object from normalized SQLAlchemy
rows.

## Configuration

`config/data-agent.yaml` controls the database Runtime specialization:

- `llm`: provider, model, temperature, timeout, Bedrock model ID.
- `prompts.sql_generation`: SQL-generation instructions.
- `messages`: fixed rejection and operational error messages.
- `query`: row limits, request timeout, question/context bounds.
- `database`: SQLAlchemy dialect, SQLGlot dialect, connect args, statement
  timeout.
- `authorization`: inbound grant mode, required grants, propagated identity
  claims.
- `capabilities`: tool-level authorization and downstream identity policy.
- `data_model`: authorized relations, columns, glossary, synonyms, categorical
  values, SQL rules, and allowed SQL functions.
- `output`: cell length bounds and redaction patterns.
- `observability`: logging behavior.

The config is non-sensitive and versioned in S3. It can change without
rebuilding the ZIP as long as the contract expected by `app/config.py` remains
stable.

## Database Guardrails

The Runtime rejects obvious unsafe input before invoking the LLM, including:

- write intents such as insert, update, delete, drop, create, alter, truncate
- requests for passwords, credentials, secrets, tokens, or API keys
- oversized questions or context payloads

The generated SQL is parsed and validated with SQLGlot. The validator enforces:

- exactly one statement
- `SELECT` or `WITH ... SELECT` only
- no `SELECT INTO`
- no `SELECT *`
- authorized relations only
- authorized columns per relation
- projected CTE and derived-table columns, including aggregate aliases, may be
  selected after their source query has been validated
- denied columns rejected
- allowed functions only
- literal integer `LIMIT` when present
- final SQL re-rendered from the validated AST

SQLGlot is a deterministic gate before execution. Database permissions remain
the final enforcement layer.

If a safe-looking question is rejected, inspect the returned rejection reason
and the generated SQL in an authorized SQL-viewer workflow. Common causes are
unconfigured SQL functions, `SELECT *`, non-literal limits, relations or columns
missing from `data_model.allowed_relations`, or SQL constructs intentionally not
supported by the validator.

## PostgreSQL Preparation

The database role is expected to be:

- a fixed technical role
- read-only
- granted only to approved schemas/views
- denied broad access to physical source tables
- constrained by statement and transaction settings

Use the templates under `postgres/` to create the role, grant authorized views,
and verify denied write access. Production deployments should use
security-barrier views where available, indexes for expected access patterns,
statement timeouts, connection limits, query monitoring, and preferably a read
replica for analytical traffic.

The PostgreSQL adapter applies read-only controls:

```sql
SET TRANSACTION READ ONLY;
SELECT set_config('statement_timeout', :timeout_ms, true);
```

Supporting another database requires:

- the SQLAlchemy driver
- a matching SQLGlot dialect
- an adapter in `app/capabilities/database/database.py`
- equivalent read-only and timeout controls

Unsupported dialects fail closed.

## Multiple Database Agents

The Gateway is shared. Each database agent should be deployed as a separate
Runtime and GatewayTarget with its own:

- config file
- database secret
- optional foundation stack for managed subnets, Runtime security group, and
  secret
- target name
- Runtime IAM role
- authorized data model
- PostgreSQL read-only role
- grants and SQL visibility policy
- subnet and security-group overrides when needed

Example:

```bash
./scripts/build.sh
DATA_AGENT_INSTANCE=cmdb CONFIG_FILE=config/cmdb-agent.yaml ./scripts/publish.sh prod
export ARTIFACT_KEY=artifacts/prod/cmdb/data-agent-REPLACE.zip
export CONFIG_KEY=config/prod/cmdb/data-agent-REPLACE.yaml
DATA_AGENT_INSTANCE=cmdb CONFIG_FILE=config/cmdb-agent.yaml ./scripts/deploy.sh prod
DATA_AGENT_INSTANCE=cmdb CONFIG_FILE=config/cmdb-agent.yaml ./scripts/smoke_test.sh prod
```

`DATA_AGENT_INSTANCE` drives the Runtime stack suffix, target name, manifest
prefix, per-instance foundation stack, per-instance Runtime IAM role name, and
smoke-test target selection. Override `TARGET_NAME` only if the Gateway target
name should differ from the instance name.

Per-agent infrastructure should usually be product-managed for new database
agents. The product creates Runtime-only subnets, the Runtime security group,
and the database secret inside the target VPC:

```json
{
  "agents": {
    "cmdb": {
      "vpc_id": "vpc-123",
      "runtime_network_mode": "managed",
      "managed_private_subnet_cidr_1": "10.10.40.0/24",
      "managed_private_subnet_cidr_2": "10.10.41.0/24",
      "database_secret_mode": "managed",
      "database_secret_name": "/data-agent/prod/cmdb/database",
      "database_secret_string": "{\"database_uri\":\"postgresql+psycopg://ROLE:REPLACE@db.internal:5432/CMDB?sslmode=verify-full\"}"
    }
  }
}
```

External resources remain supported when subnets, security groups, or the
database secret are owned outside the product:

```json
{
  "agents": {
    "cmdb": {
      "database_secret_mode": "external",
      "database_secret_arn": "arn:aws:secretsmanager:eu-west-1:111122223333:secret:/data-agent/prod/cmdb",
      "runtime_network_mode": "external",
      "private_subnet_ids": "subnet-a,subnet-b",
      "runtime_security_group_ids": "sg-cmdb"
    }
  }
}
```

## Instance Checklist

Before deploying a new database agent:

- Create database-specific security-barrier views that expose only approved
  relations and columns.
- Create a dedicated read-only database role and grant only the approved views.
- Provide the read-only role connection string as
  `database_secret_string` when `database_secret_mode=managed`. The product
  creates the Secrets Manager secret and writes the validated JSON value before
  deploying the Runtime. Use `database_secret_arn` only in `external` mode.
- Create a dedicated config YAML with prompts, data model, glossary, synonyms,
  categorical values, SQL rules, query limits, output controls, and capability
  grants.
- Add `agents.<instance>` overrides to the parameter file. Use `managed` mode
  for product-owned per-agent subnets, Runtime security group, and secret. Use
  `external` mode only when those resources are managed outside the product.
- Run `build.sh` once for the code artifact.
- Run `publish.sh` with `DATA_AGENT_INSTANCE=<instance>` and
  `CONFIG_FILE=<path-to-config>`.
- Export the printed `ARTIFACT_KEY` and `CONFIG_KEY`.
- Run `deploy.sh` with the same `DATA_AGENT_INSTANCE` and `CONFIG_FILE`.
- Run `smoke_test.sh` and verify Gateway lists/calls the intended target.
- Review audit logs, database query logs, and IAM/secret access before broad
  access is granted.

## Output Contract

The Runtime returns deterministic JSON. On success, the canonical `data`
payload contains:

- normalized rows
- row count
- truncation flag
- SQL assumptions from the generation step

The top-level response includes status, trace ID, elapsed-time metadata,
relations used, row count, warnings, and optional SQL when the caller has the
SQL visibility grant and requests it. Rejections and operational errors return
a fixed `message`.

SQL validation proves that the generated query is syntactically valid,
read-only, and restricted to the authorized model. Semantic fit to the user's
intent is represented through assumptions and warnings rather than a confidence
score.

Common database scalar types such as timestamps and decimals are converted to
JSON-compatible values. Denied columns and configured redaction patterns are
applied before rows are returned.

Human interfaces render or summarize `data` outside the trusted Runtime, with a
separate data-governance review for any LLM-based renderer.

## Data Governance

The LLM receives the user's question, bounded context, and authorized data-model
metadata to generate SQL. Query result rows remain in the Runtime response path
and are governed by output bounds, redaction, and JSON normalization.

Bedrock is the default provider, but its use still requires review of data
classification, regional processing, logging, retention, and model access.
Enabling OpenAI additionally requires explicit approval for external-provider
processing, data residency, contractual terms, and permitted fields.
