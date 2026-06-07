from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class AskDatabaseRequest(BaseModel):
    """Validated public tool input."""

    question: str = Field(min_length=3, max_length=2000)
    max_rows: int | None = Field(default=None, ge=1)
    include_sql: bool = False
    context: dict[str, str] | None = None

    @field_validator("context")
    @classmethod
    def validate_context_shape(
        cls, value: dict[str, str] | None
    ) -> dict[str, str] | None:
        """Reject non-string context values before they can reach prompt rendering."""

        if value is not None and not all(
            isinstance(key, str) and isinstance(item, str) for key, item in value.items()
        ):
            raise ValueError("context_strings_required")
        return value


class AskDatabaseResponse(BaseModel):
    """Stable public tool response contract."""

    status: Literal["ok", "rejected", "error"]
    data: dict[str, Any] | None = None
    message: str | None = None
    sql: str | None = None
    relations_used: list[str] = Field(default_factory=list)
    row_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    trace_id: str
    elapsed_ms: int
    rejection_reason: str | None = None


class SqlCandidate(BaseModel):
    """Structured but untrusted output produced by the SQL-generation model."""

    sql: str
    assumptions: list[str] = Field(default_factory=list)


class QueryResult(BaseModel):
    """Normalized representation of a database query result."""

    rows: list[dict[str, Any]]
