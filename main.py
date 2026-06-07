import sys
import time

_BOOT_STARTED = time.perf_counter()


def _boot_log(message: str) -> None:
    elapsed_ms = int((time.perf_counter() - _BOOT_STARTED) * 1000)
    print(f"BOOT {elapsed_ms}ms {message}", file=sys.stderr, flush=True)


_boot_log("main.py start")

import asyncio
import logging
import os
import uuid
from datetime import date, datetime, time as datetime_time
from decimal import Decimal
from typing import Any

_boot_log("stdlib imports complete")
_boot_log("importing mcp.server.fastmcp")
from mcp.server.fastmcp import Context, FastMCP

_boot_log("imported mcp.server.fastmcp")
_boot_log("importing app.authorization")
from app.authorization import (
    CallerIdentity,
    claim_values,
    identity_from_header,
    verify_gateway_header_signature,
)

_boot_log("imported app.authorization")
_boot_log("importing app.audit")
from app.audit import emit

_boot_log("imported app.audit")
_boot_log("importing database execution")
from app.capabilities.database.database import execute_read_only_sql

_boot_log("imported database execution")
_boot_log("importing llm helpers")
from app.capabilities.database.llm import generate_sql

_boot_log("imported llm helpers")
_boot_log("importing database models")
from app.capabilities.database.models import AskDatabaseRequest, AskDatabaseResponse

_boot_log("imported database models")
_boot_log("importing database security")
from app.capabilities.database.security import (
    has_scope,
    normalize_rows,
    validate_context,
    validate_question,
)

_boot_log("imported database security")
_boot_log("importing sql validator")
from app.capabilities.database.sql_validator import validate_sql

_boot_log("imported sql validator")
_boot_log("importing config helpers")
from app.config import AppConfig, load_config, load_secret

_boot_log("imported config helpers")

logging.basicConfig(level=logging.INFO)
_boot_log("logging configured")
# AgentCore Runtime expects an MCP server listening on 0.0.0.0:8000/mcp.
_boot_log("creating FastMCP server")
mcp = FastMCP(
    "read-only-data-agent",
    host="0.0.0.0",
    port=8000,
    streamable_http_path="/mcp",
    stateless_http=True,
)
_boot_log("created FastMCP server")


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


def _response_rows(
    rows: list[dict[str, Any]], source_row_count: int, config: AppConfig
) -> tuple[list[dict[str, Any]], bool]:
    """Return the rows visible in the response and whether anything was omitted."""

    response_rows = rows[: config.output.max_summary_rows]
    return response_rows, source_row_count > len(response_rows)


def _json_payload(
    rows: list[dict[str, Any]],
    row_count: int,
    truncated: bool,
    assumptions: list[str],
) -> dict[str, Any]:
    """Build deterministic query output without an LLM summarization pass."""

    return {
        "rows": _json_safe(rows),
        "row_count": row_count,
        "truncated": truncated,
        "assumptions": assumptions,
    }


def _json_safe(value: Any) -> Any:
    """Convert database scalar values into JSON-compatible values."""

    if isinstance(value, datetime | date | datetime_time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


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
        return AskDatabaseResponse(
            trace_id=trace_id,
            elapsed_ms=elapsed_ms,
            **kwargs,
        ).model_dump(exclude_none=True)

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
            response_rows, response_truncated = await timed_phase(
                "response_rows_ms", lambda: _response_rows(rows, len(result.rows), config)
            )
            data = await timed_phase(
                "json_payload_ms",
                lambda: _json_payload(
                    response_rows,
                    len(rows),
                    response_truncated,
                    candidate.assumptions,
                ),
            )
        can_show_sql = request.include_sql and (
            config.query.allow_sql_by_default
            or has_scope(
                grants,
                capability.sql_viewer_grant or config.authorization.sql_viewer_scope,
            )
        )
        warnings = [*candidate.assumptions]
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
            data=data,
            sql=validated.sql if can_show_sql else None,
            relations_used=validated.relations_used,
            row_count=len(rows),
            warnings=warnings,
        )
    except PermissionError as exc:
        emit("ask_database_rejected", trace_id=trace_id, reason=type(exc).__name__)
        return response(
            status="rejected",
            message=config.messages.rejected,
            rejection_reason=str(exc),
        )
    except ValueError as exc:
        emit("ask_database_rejected", trace_id=trace_id, reason=type(exc).__name__)
        return response(
            status="rejected",
            message=config.messages.rejected,
            rejection_reason=str(exc),
        )
    except Exception:
        logging.exception("ask_database failed trace_id=%s", trace_id)
        emit("ask_database_failed", trace_id=trace_id)
        return response(
            status="error",
            message=config.messages.error,
        )


if __name__ == "__main__":
    _boot_log("starting FastMCP run")
    mcp.run(transport="streamable-http")
