# AWS Data AgentCore

Modular tool hub for Amazon Bedrock AgentCore. The repository contains a shared
Gateway foundation and a read-only database Runtime exposed as the MCP tool
`ask_database`.

The architecture separates shared routing concerns from target-specific
capability contracts:

- **Gateway hub**: authenticates callers, routes MCP tool traffic, propagates a
  bounded authorization context, and hosts multiple targets in one environment.
- **Runtime targets**: implement one capability contract each, with their own
  IAM role, secrets, network posture, configuration, and deterministic
  guardrails.
- **Database Runtime**: turns natural-language questions into validated
  read-only SQL, executes through SQLAlchemy Core, and returns a bounded
  canonical `data` object. Human-facing rendering belongs outside the trusted
  Runtime.

## Documentation Map

Start here, then follow the document that matches the layer you are changing:

| Document | Use For |
| --- | --- |
| [Gateway hub](docs/gateway_hub.md) | Shared Gateway, JWT/OIDC authorization, request interceptor, target contracts, service vs OBO identity modes. |
| [Database Runtime](docs/database_runtime.md) | `ask_database`, config schema, SQL validation, PostgreSQL preparation, multiple database-agent instances. |
| [Security architecture](docs/security_architecture.md) | End-to-end trust boundaries, threat model, IAM roles, residual risks, production controls. |
| [Microsoft Entra ID setup](docs/entra_id_setup.md) | Practical Entra two-app setup, scopes/app roles, token validation, smoke-test token flow. |

## Repository Layout

```text
aws-data-agentcore/
├── app/                     Runtime Python code
│   ├── authorization.py      Shared internal context JWT and grant helpers
│   ├── audit.py              Shared structured audit helper
│   ├── config.py             Shared validated configuration model
│   └── capabilities/
│       └── database/         Read-only database capability implementation
├── config/                  Versioned non-sensitive Runtime configuration
├── docs/                    Architecture, security, and setup guides
├── infrastructure/          CloudFormation stacks
│   ├── agent-foundation.yaml Per-agent managed subnets, Runtime SG, and secret
│   ├── bootstrap.yaml        Shared Gateway hub, bucket, interceptor, context secret
│   ├── gateway-identity-permissions.yaml
│   │                         Optional Gateway permissions for OBO targets
│   ├── parameters.json       Example parameter file
│   ├── private-endpoints.yaml
│   │                         Private AWS service endpoints for Runtime access
│   ├── runtime.yaml          One database Runtime instance
│   ├── target.yaml           IAM GatewayTarget for Runtime MCP endpoint
│   └── target-mcp-oauth-obo.yaml
│                            Template for OBO MCP targets
├── postgres/                Generic PostgreSQL read-only templates
├── scripts/                 Build, publish, deploy, CLI, and smoke-test tools
└── tests/                   Unit tests for critical controls
```

## What Is Shared And What Is Per Target

The bootstrap stack is shared per environment:

- AgentCore Gateway
- Gateway IAM role
- JWT/OIDC authorizer settings
- request interceptor Lambda
- internal context JWT signing secret
- versioned artifact/config bucket

Each Runtime target is deployed separately:

- optional per-agent managed subnets, Runtime security group, and database
  secret
- Runtime stack and AgentCore Runtime
- Runtime IAM role
- S3 artifact and config keys
- target-specific secrets
- subnet and security-group settings
- GatewayTarget registration
- capability configuration and guardrails

The database deployment scripts create `GATEWAY_IAM_ROLE` Runtime targets. OBO
targets use a dedicated deployment path and credential-provider configuration.
Private AWS service endpoints are ensured before the Runtime by default. The
deployment first looks for an existing product-managed endpoint stack in the
same environment and VPC, reuses it when found, and otherwise creates one. Set
`create_private_service_endpoints=false` only when equivalent endpoints and S3
route table associations are managed outside the product.

The committed configuration and parameter files are deployment templates. Before
production, replace demo placeholders, review IAM and VPC endpoint policy scope
against the target account, and document any wildcard permissions that are
required by the AWS service contract.

## Prerequisites

- AWS CLI authenticated with permissions for CloudFormation, S3, IAM, Secrets
  Manager, Bedrock, AgentCore, and CloudWatch Logs.
- Python 3.13 and `zip`.
- A private VPC route to the target database.
- VPC ID. Runtime subnet IDs, Runtime security group IDs, and database secret
  ARN can be supplied externally, or created per agent with
  `runtime_network_mode=managed` and `database_secret_mode=managed`.
- An OIDC/JWT provider for authenticating Gateway consumers.
- Database-specific authorized views and a read-only technical role.

OpenAI is supported only after an explicit data-governance decision. Bedrock is
the default provider.

## Quick Deployment Flow

1. Configure the database specialization in `config/data-agent.yaml`.
2. Create approved database views and apply the PostgreSQL templates under
   `postgres/`.
3. Decide whether the product manages Runtime network and database secret
   resources. The demo and recommended database-agent flow use
   `runtime_network_mode=managed` and `database_secret_mode=managed`.
4. Complete `infrastructure/parameters.json` or an environment-specific
   `parameters.<environment>.json`.
5. Build, publish, deploy, and smoke test:

```bash
./scripts/bootstrap.sh prod
./scripts/build.sh
./scripts/publish.sh prod
export ARTIFACT_KEY=artifacts/prod/data-agent-REPLACE.zip
export CONFIG_KEY=config/prod/data-agent-REPLACE.yaml
./scripts/deploy.sh prod
./scripts/smoke_test.sh prod
```

`publish.sh` prints the exact `ARTIFACT_KEY` and `CONFIG_KEY` values to export
before `deploy.sh`. The committed parameter file is a template and is not
expected to deploy unchanged.

For local validation before publishing, run:

```bash
make lint
make test
```

## Database Runtime Instance Example

For the default database-agent flow, the product creates the per-agent database
secret and writes the connection JSON after the foundation stack exists:

```json
{
  "database_secret_mode": "managed",
  "database_secret_name": "/data-agent/prod/database/database",
  "database_secret_string": "{\"database_uri\":\"postgresql+psycopg://ROLE:REPLACE@db.internal:5432/DATABASE?sslmode=verify-full\"}"
}
```

For multiple database agents, deploy one Runtime and GatewayTarget per
instance. Each instance should have its own config file, database secret,
read-only database role, Runtime IAM role, and authorized data model:

```bash
./scripts/build.sh
DATA_AGENT_INSTANCE=cmdb CONFIG_FILE=config/cmdb-agent.yaml ./scripts/publish.sh prod
export ARTIFACT_KEY=artifacts/prod/cmdb/data-agent-REPLACE.zip
export CONFIG_KEY=config/prod/cmdb/data-agent-REPLACE.yaml
DATA_AGENT_INSTANCE=cmdb CONFIG_FILE=config/cmdb-agent.yaml ./scripts/deploy.sh prod
DATA_AGENT_INSTANCE=cmdb CONFIG_FILE=config/cmdb-agent.yaml ./scripts/smoke_test.sh prod
```

Per-instance infrastructure overrides live under `agents` in the parameter
file. Product-managed resources are the natural path for new database agents:

```json
{
  "region": "eu-west-1",
  "artifact_bucket_name": "corp-data-agent-artifacts",
  "jwt_discovery_url": "https://login.example/.well-known/openid-configuration",
  "jwt_allowed_audience": "api://data-agent",
  "required_scope": "data:read",
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

Existing network and secret resources can still be supplied explicitly when an
environment has a separate ownership model:

```json
{
  "region": "eu-west-1",
  "artifact_bucket_name": "corp-data-agent-artifacts",
  "jwt_discovery_url": "https://login.example/.well-known/openid-configuration",
  "jwt_allowed_audience": "api://data-agent",
  "required_scope": "data:read",
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

Managed foundation resources are deployed in a per-agent stack named
`data-agent-foundation-<environment>-<instance>`, so adding or updating one
agent does not redeploy existing agent Runtime stacks.

For managed secrets, CloudFormation creates the secret resource and `deploy.sh`
writes the validated `database_secret_string` afterward through Secrets
Manager. Do not create a separate preexisting secret for the demo; pass the
database URI as `database_secret_string` or through the example wrapper's
`DATABASE_URI`.

Private service endpoints are shared by environment and VPC. By default the
stack name is `data-agent-private-endpoints-<environment>-<vpc-id>`, but older
product endpoint stacks with the same VPC are reused automatically. Set
`private_service_endpoint_stack_name` only when a specific product-managed
endpoint stack must be selected.

The endpoint stack creates a shared Runtime access security group and allows
interface endpoint ingress from that SG. Each Runtime is launched with both its
agent-specific Runtime SGs and this shared access SG, so new agents can use the
existing product endpoints without opening endpoint access to the whole VPC.
`endpoint_ingress_cidr` remains available only as an explicit override.

See [Database Runtime](docs/database_runtime.md) for the full instance
checklist and configuration contract.

## Manual Gateway CLI

After deployment, use a bearer token for the configured OIDC provider:

```bash
export BEARER_TOKEN="$(az account get-access-token \
  --tenant REPLACE_TENANT_ID \
  --scope api://REPLACE_API_APP_ID/data:read \
  --query accessToken \
  --output tsv)"
./scripts/agent_cli.sh prod
```

The Gateway request interceptor derives a stable `Mcp-Session-Id` from verified
identity claims for Runtime microVM affinity, overwriting any client-provided
value. The CLI also preserves returned MCP session headers for protocol
compatibility, but authorization still relies on Gateway JWT validation and
the short-lived internal `x-data-agent-context` JWT.

## Configuration Summary

The Runtime receives only bootstrap references through environment variables:

- `CONFIG_BUCKET`
- `CONFIG_KEY`
- `DATABASE_SECRET_ARN`
- `INTERNAL_CONTEXT_SIGNING_SECRET_ARN`
- `INTERNAL_CONTEXT_AUDIENCE`
- `OPENAI_SECRET_ARN` when OpenAI is enabled
- `APP_ENV`
- `AWS_REGION`

`config/data-agent.yaml` is non-sensitive and versioned in S3. It contains
model selection, prompts for SQL generation, database dialect settings,
authorization policy, capability declarations, authorized relations, query
limits, output controls, and observability settings.

These values can change without rebuilding the ZIP as long as the expected
configuration contract remains stable.

## Development

Run tests from the repository root:

```bash
python3 -m pytest
```

Useful implementation boundaries:

- Put new domain modules under `app/capabilities/<module>/`.
- Promote code to top-level `app/` only when at least two modules need it.
- Keep target-specific guardrails local to the capability package.
- Keep Gateway/OBO setup out of the database Runtime path unless the target
  actually needs delegated downstream access.

## Artifact Cleanup

Versioned artifacts and configuration keys are published immutably. Use
`scripts/cleanup_artifacts.py` to remove keys that are not referenced by the
active manifest, the retained manifest window, or Runtime stack parameters. It
runs as a dry run unless `--apply` is passed.

## AWS References

- [Direct code deployment for Python](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html)
- [Deploy MCP servers in AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-mcp.html)
- [MCP server targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-MCPservers.html)
- [AWS::BedrockAgentCore::Runtime](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-runtime.html)
- [AWS::BedrockAgentCore::Gateway](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-gateway.html)
