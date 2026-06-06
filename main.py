import asyncio
import logging
import os
import time
import uuid
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from app.authorization import (
    CallerIdentity,
    claim_values,
    identity_from_header,
    verify_gateway_header_signature,
)
from app.audit import emit
from app.capabilities.database.database import execute_read_only_sql
from app.capabilities.database.llm import generate_sql, summarize_results
from app.capabilities.database.models import AskDatabaseRequest, AskDatabaseResponse
from app.capabilities.database.security import (
    has_scope,
    normalize_rows,
    validate_context,
    validate_question,
)
from app.capabilities.database.sql_validator import validate_sql
from app.config import AppConfig, load_config, load_secret


logging.basicConfig(level=logging.INFO)
# AgentCore Runtime expects an MCP server listening on 0.0.0.0:8000/mcp.
mcp = FastMCP(
    "read-only-data-agent",
    host="0.0.0.0",
    port=8000,
    streamable_http_path="/mcp",
    stateless_http=True,
)


def _gateway_header_secret() -> str:
    """Load the shared secret used to authenticate Gateway-propagated headers."""

    secret = load_secret(os.environ["GATEWAY_HEADER_SIGNING_SECRET_ARN"])
    return str(secret["secret"])


def _gateway_signature_ttl_seconds() -> int:
    """Return the accepted age for signed Gateway headers."""

    return int(os.environ.get("GATEWAY_HEADER_SIGNATURE_TTL_SECONDS", "300"))


def _trusted_gateway_context(
    ctx: Context | None, signing_secret: str
) -> tuple[set[str], CallerIdentity]:
    """Read trusted authorization grants and identity from signed Gateway headers."""

    if ctx is None:
        raise PermissionError("missing_gateway_context")
    try:
        request = ctx.request_context.request
        grants = request.headers.get("x-data-agent-grants", "")
        identity = request.headers.get("x-data-agent-identity", "")
        issued_at = request.headers.get("x-data-agent-issued-at", "")
        signature = request.headers.get("x-data-agent-signature", "")
    except AttributeError:
        raise PermissionError("missing_gateway_context") from None
    if not verify_gateway_header_signature(
        signing_secret,
        grants,
        identity,
        issued_at,
        signature,
        ttl_seconds=_gateway_signature_ttl_seconds(),
    ):
        raise PermissionError("invalid_gateway_signature")
    return set(claim_values(grants)), identity_from_header(identity)


def _summary_input(
    rows: list[dict[str, Any]], source_row_count: int, config: AppConfig
) -> tuple[list[dict[str, Any]], bool]:
    """Return the rows visible to the summarizer and whether anything was omitted."""

    summary_rows = rows[: config.output.max_summary_rows]
    return summary_rows, source_row_count > len(summary_rows)


@mcp.tool(
    name="ask_database",
    description=(
        "Answers natural-language questions about the configured database model. "
        "Read-only questions only; direct SQL is not accepted."
    ),
)
async def ask_database(
    question: str,
    ctx: Context,
    max_rows: int | None = None,
    include_sql: bool = False,
    context: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Handle one stateless, read-only database question."""

    started = time.perf_counter()
    trace_id = str(uuid.uuid4())
    phase_timings_ms: dict[str, int] = {}

    async def timed_phase(name: str, operation: Any) -> Any:
        phase_started = time.perf_counter()
        try:
            if callable(operation):
                value = operation()
            else:
                value = operation
            if hasattr(value, "__await__"):
                return await value
            return value
        finally:
            phase_timings_ms[name] = int((time.perf_counter() - phase_started) * 1000)

    config = load_config()
    phase_timings_ms["config_load_ms"] = int((time.perf_counter() - started) * 1000)
    capability = config.capability("ask_database")

    def response(**kwargs: Any) -> dict[str, Any]:
        # Compute elapsed time at the last possible moment for every response path.
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return AskDatabaseResponse(trace_id=trace_id, elapsed_ms=elapsed_ms, **kwargs).model_dump()

    try:
        request = await timed_phase(
            "request_validation_ms",
            lambda: AskDatabaseRequest(
                question=question,
                max_rows=max_rows,
                include_sql=include_sql,
                context=context,
            ),
        )
        grants, caller = await timed_phase(
            "authorization_ms",
            lambda: _trusted_gateway_context(ctx, _gateway_header_secret()),
        )
        # Authorization must fail closed if the trusted Gateway scope header is absent.
        if not all(has_scope(grants, grant) for grant in capability.required_grants):
            raise PermissionError("missing_required_scope")
        validate_question(request.question, config)
        validate_context(request.context, config)
        async with asyncio.timeout(config.query.timeout_seconds):
            bounded_rows = min(
                request.max_rows or config.query.default_max_rows,
                config.query.absolute_max_rows,
            )
            # LLM output is always treated as untrusted until validate_sql succeeds.
            candidate = await timed_phase(
                "sql_generation_ms",
                generate_sql(config, request.question, bounded_rows, request.context),
            )
            validated = await timed_phase(
                "sql_validation_ms",
                lambda: validate_sql(candidate.sql, config, bounded_rows),
            )
            result = await timed_phase(
                "database_ms",
                execute_read_only_sql(validated.sql, config),
            )
            rows = await timed_phase(
                "result_normalization_ms", lambda: normalize_rows(result.rows, config)
            )
            summary_rows, summary_truncated = await timed_phase(
                "summary_input_ms", lambda: _summary_input(rows, len(result.rows), config)
            )
            summary = await timed_phase(
                "result_summary_ms",
                summarize_results(
                    config,
                    request.question,
                    summary_rows,
                    candidate.assumptions,
                    truncated=summary_truncated,
                ),
            )
        can_show_sql = request.include_sql and (
            config.query.allow_sql_by_default
            or has_scope(
                grants,
                capability.sql_viewer_grant or config.authorization.sql_viewer_scope,
            )
        )
        warnings = [*candidate.assumptions, *summary.warnings]
        emit(
            "ask_database_completed",
            trace_id=trace_id,
            provider=config.llm.provider,
            model=config.llm.model,
            relations=validated.relations_used,
            row_count=len(rows),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            timings_ms=phase_timings_ms,
            identity_mode=capability.identity_mode,
            **caller.audit_fields(),
        )
        return response(
            status="ok",
            answer=summary.answer,
            sql=validated.sql if can_show_sql else None,
            relations_used=validated.relations_used,
            row_count=len(rows),
            warnings=warnings,
            confidence=summary.confidence,
        )
    except PermissionError as exc:
        emit("ask_database_rejected", trace_id=trace_id, reason=type(exc).__name__)
        return response(
            status="rejected",
            answer=config.messages.rejected,
            rejection_reason=str(exc),
        )
    except ValueError as exc:
        emit("ask_database_rejected", trace_id=trace_id, reason=type(exc).__name__)
        return response(
            status="rejected",
            answer=config.messages.rejected,
            rejection_reason=str(exc),
        )
    except Exception:
        logging.exception("ask_database failed trace_id=%s", trace_id)
        emit("ask_database_failed", trace_id=trace_id)
        return response(
            status="error",
            answer=config.messages.error,
        )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
