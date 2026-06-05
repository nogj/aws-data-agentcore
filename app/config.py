import json
import os
from typing import Any, Literal

import boto3
import yaml
from pydantic import BaseModel, Field


class RelationConfig(BaseModel):
    name: str
    description: str
    columns: list[str]


class DataModelConfig(BaseModel):
    """Authorized logical model exposed to the agent."""

    description: str
    allowed_relations: list[RelationConfig]
    denied_columns: list[str] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    synonyms: dict[str, str] = Field(default_factory=dict)
    sql_rules: list[str] = Field(default_factory=list)
    allowed_functions: list[str] = Field(default_factory=list)


class LlmConfig(BaseModel):
    """Runtime model selection and invocation settings."""

    provider: str
    model: str
    temperature: float = 0
    timeout_seconds: int = 30
    bedrock_model_id: str | None = None


class PromptTemplateConfig(BaseModel):
    """A system/user prompt pair loaded from the non-sensitive S3 configuration."""

    system: str
    user: str


class PromptsConfig(BaseModel):
    """All LLM instructions used by the agent."""

    sql_generation: PromptTemplateConfig
    result_summary: PromptTemplateConfig


class MessagesConfig(BaseModel):
    """Fixed operational messages that never require an LLM invocation."""

    rejected: str
    error: str


class QueryConfig(BaseModel):
    default_max_rows: int = 50
    absolute_max_rows: int = 200
    timeout_seconds: int = 30
    allow_sql_by_default: bool = False
    max_question_length: int = 2000
    max_context_items: int = 10
    max_context_key_length: int = 100
    max_context_value_length: int = 1000


class DatabaseConfig(BaseModel):
    """Connection and SQL dialect settings for the configured database."""

    dialect: str
    sqlglot_dialect: str
    secret_uri_key: str = "database_uri"
    connect_args: dict[str, Any] = Field(default_factory=dict)
    statement_timeout_ms: int = 30000


class AuthorizationConfig(BaseModel):
    mode: Literal["scopes", "claims"] = "scopes"
    required_scope: str = "data:read"
    sql_viewer_scope: str = "data:sql:read"
    accepted_claims: list[str] = Field(default_factory=lambda: ["scope", "scp"])
    identity_claims: list[str] = Field(
        default_factory=lambda: ["sub", "oid", "preferred_username", "appid", "azp", "tid"]
    )


class CapabilityConfig(BaseModel):
    """Authorization and downstream identity policy for one exposed capability."""

    name: str
    target: str
    identity_mode: Literal["service", "on_behalf_of_user"] = "service"
    required_grants: list[str]
    sql_viewer_grant: str | None = None
    downstream_audience: str | None = None
    credential_provider_name: str | None = None


class OutputConfig(BaseModel):
    max_cell_length: int = 1000
    max_summary_rows: int = 50
    redact_patterns: list[str] = Field(default_factory=list)


class ObservabilityConfig(BaseModel):
    log_level: str = "INFO"
    log_question: bool = True
    log_sql: bool = False


class AppConfig(BaseModel):
    """Validated representation of the versioned agent configuration."""

    version: str
    agent: dict[str, Any]
    llm: LlmConfig
    prompts: PromptsConfig
    messages: MessagesConfig
    query: QueryConfig
    database: DatabaseConfig
    authorization: AuthorizationConfig
    capabilities: list[CapabilityConfig] = Field(default_factory=list)
    data_model: DataModelConfig
    output: OutputConfig
    observability: ObservabilityConfig

    def capability(self, name: str) -> CapabilityConfig:
        """Return the configured policy for a named capability."""

        for capability in self.capabilities:
            if capability.name == name:
                return capability
        return CapabilityConfig(
            name=name,
            target="data-agent",
            identity_mode="service",
            required_grants=[self.authorization.required_scope],
            sql_viewer_grant=self.authorization.sql_viewer_scope,
        )


def load_config() -> AppConfig:
    """Load the active non-sensitive configuration on every invocation."""

    bucket = os.environ["CONFIG_BUCKET"]
    key = os.environ["CONFIG_KEY"]
    body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    return AppConfig.model_validate(yaml.safe_load(body))


def load_secret(secret_arn: str) -> dict[str, Any]:
    """Retrieve a JSON secret without persisting it outside process memory."""

    value = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
    return json.loads(value["SecretString"])
