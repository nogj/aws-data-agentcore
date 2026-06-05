import os
import json
from typing import Any

from langchain_aws import ChatBedrockConverse
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from app.config import AppConfig, PromptTemplateConfig, load_secret
from app.capabilities.database.models import SqlCandidate, SummaryCandidate


def _chat_model(config: AppConfig):
    """Create the configured LangChain chat model without leaking provider details."""

    if config.llm.provider == "openai":
        secret = load_secret(os.environ["OPENAI_SECRET_ARN"])
        return ChatOpenAI(
            model=config.llm.model,
            api_key=secret["api_key"],
            temperature=config.llm.temperature,
            timeout=config.llm.timeout_seconds,
        )
    if config.llm.provider == "bedrock":
        return ChatBedrockConverse(
            model=config.llm.bedrock_model_id or config.llm.model,
            temperature=config.llm.temperature,
            timeout=config.llm.timeout_seconds,
        )
    raise ValueError(f"Proveedor LLM no soportado: {config.llm.provider}")


def _schema_context(config: AppConfig) -> str:
    """Serialize the authorized model as data, keeping instructions in configuration."""

    return json.dumps(config.data_model.model_dump(), ensure_ascii=False, indent=2)


def _prompt(template: PromptTemplateConfig) -> ChatPromptTemplate:
    """Build a LangChain prompt exclusively from versioned configuration."""

    return ChatPromptTemplate.from_messages(
        [("system", template.system), ("human", template.user)]
    )


async def generate_sql(
    config: AppConfig,
    question: str,
    max_rows: int,
    context: dict[str, str] | None,
) -> SqlCandidate:
    """Generate an untrusted SQL candidate that must still pass deterministic validation."""

    prompt = _prompt(config.prompts.sql_generation)
    chain = prompt | _chat_model(config).with_structured_output(SqlCandidate)
    return await chain.ainvoke(
        {
            "schema_context": _schema_context(config),
            "question": question,
            "context": context or {},
            "max_rows": max_rows,
        }
    )


async def summarize_results(
    config: AppConfig,
    question: str,
    rows: list[dict[str, Any]],
    assumptions: list[str],
    truncated: bool,
) -> SummaryCandidate:
    """Summarize bounded results in the same language used by the question."""

    prompt = _prompt(config.prompts.result_summary)
    chain = prompt | _chat_model(config).with_structured_output(SummaryCandidate)
    return await chain.ainvoke(
        {
            "question": question,
            "assumptions": assumptions,
            "rows": rows,
            "truncated": truncated,
        }
    )
