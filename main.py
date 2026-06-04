import asyncio
import logging
import time
import uuid
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from app.audit import emit
from app.config import load_config
from app.database import execute_read_only_sql
from app.llm import generate_sql, summarize_results
from app.models import AskDatabaseRequest, AskDatabaseResponse
from app.security import has_scope, normalize_rows, validate_context, validate_question
from app.sql_validator import validate_sql


logging.basicConfig(level=logging.INFO)
# AgentCore Runtime expects an MCP server listening on 0.0.0.0:8000/mcp.
mcp = FastMCP(
    "read-only-data-agent",
    host="0.0.0.0",
    port=8000,
    streamable_http_path="/mcp",
    stateless_http=True,
)


def _scopes_from_context(ctx: Context | None) -> set[str]:
    """Read trusted scopes injected by a Gateway interceptor, when configured."""

    if ctx is None:
        return set()
    try:
        request = ctx.request_context.request
        raw = request.headers.get("x-data-agent-scopes", "")
        return set(raw.split())
    except AttributeError:
        return set()


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
    config = load_config()
    scopes = _scopes_from_context(ctx)

    def response(**kwargs: Any) -> dict[str, Any]:
        # Compute elapsed time at the last possible moment for every response path.
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return AskDatabaseResponse(trace_id=trace_id, elapsed_ms=elapsed_ms, **kwargs).model_dump()

    try:
        request = AskDatabaseRequest(
            question=question,
            max_rows=max_rows,
            include_sql=include_sql,
            context=context,
        )
        # Authorization must fail closed if the trusted Gateway scope header is absent.
        if not has_scope(scopes, config.authorization.required_scope):
            raise PermissionError("missing_required_scope")
        validate_question(request.question, config)
        validate_context(request.context, config)
        async with asyncio.timeout(config.query.timeout_seconds):
            bounded_rows = min(
                request.max_rows or config.query.default_max_rows,
                config.query.absolute_max_rows,
            )
            # LLM output is always treated as untrusted until validate_sql succeeds.
            candidate = await generate_sql(
                config, request.question, bounded_rows, request.context
            )
            validated = validate_sql(candidate.sql, config, bounded_rows)
            result = await execute_read_only_sql(validated.sql, config)
            rows = normalize_rows(result.rows, config)
            summary = await summarize_results(
                config,
                request.question,
                rows[: config.output.max_summary_rows],
                candidate.assumptions,
                truncated=len(result.rows) > len(rows),
            )
        can_show_sql = request.include_sql and (
            config.query.allow_sql_by_default
            or has_scope(scopes, config.authorization.sql_viewer_scope)
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
