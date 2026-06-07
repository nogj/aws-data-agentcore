# Database Runtime

The database Runtime is the first concrete target behind the Gateway hub. It
exposes one MCP tool, `ask_database`, for read-only natural-language questions
over an approved database model.

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

Future modules should be added under `app/capabilities/<module>/`. Shared code
should move to top-level `app/` only when at least two modules genuinely need
it.

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
    Runtime-->>Client: Deterministic JSON, optional SQL when authorized
```

The LLM is used to generate a SQL candidate. It is not trusted for
authorization and the current Runtime does not make a second LLM call to
summarize rows. The response is built from normalized SQLAlchemy rows.

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
- denied columns rejected
- allowed functions only
- literal integer `LIMIT` when present
- final SQL re-rendered from the validated AST

SQLGlot is a deterministic gate before execution. It does not replace database
permissions.

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

The current adapter applies PostgreSQL read-only controls:

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
prefix, per-instance Runtime IAM role name, and smoke-test target selection.
Override `TARGET_NAME` only if the Gateway target name should differ from the
instance name.

## Instance Checklist

Before deploying a new database agent:

- Create database-specific security-barrier views that expose only approved
  relations and columns.
- Create a dedicated read-only database role and grant only the approved views.
- Create a Secrets Manager secret under `/data-agent/<environment>/<instance>`
  containing that role's connection string.
- Create a dedicated config YAML with prompts, data model, glossary, synonyms,
  categorical values, SQL rules, query limits, output controls, and capability
  grants.
- Add `agents.<instance>` overrides to the parameter file when the database
  secret, subnets, or security groups differ from top-level defaults.
- Run `build.sh` once for the code artifact.
- Run `publish.sh` with `DATA_AGENT_INSTANCE=<instance>` and
  `CONFIG_FILE=<path-to-config>`.
- Export the printed `ARTIFACT_KEY` and `CONFIG_KEY`.
- Run `deploy.sh` with the same `DATA_AGENT_INSTANCE` and `CONFIG_FILE`.
- Run `smoke_test.sh` and verify Gateway lists/calls the intended target.
- Review audit logs, database query logs, and IAM/secret access before broad
  access is granted.

## Output Contract

The Runtime returns deterministic JSON containing:

- normalized rows
- row count
- truncation flag
- SQL assumptions from the generation step
- optional SQL only when the caller has the SQL visibility grant and requests it
- trace and elapsed-time metadata

Common database scalar types such as timestamps and decimals are converted to
JSON-compatible values. Denied columns and configured redaction patterns are
applied before rows are returned.

## Data Governance

The LLM receives the user's question, bounded context, and authorized data-model
metadata to generate SQL. Query result rows are not sent to a second LLM
summarization pass in the current Runtime.

Bedrock is the default provider, but its use still requires review of data
classification, regional processing, logging, retention, and model access.
Enabling OpenAI additionally requires explicit approval for external-provider
processing, data residency, contractual terms, and permitted fields.
