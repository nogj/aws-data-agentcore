import re
from typing import Any

from .config import AppConfig


OUT_OF_SCOPE_PATTERNS = [
    r"(?i)\b(insert|update|delete|drop|alter|create|truncate|grant|revoke)\b",
    r"(?i)\b(borra|borrar|elimina|eliminar|actualiza|actualizar|modifica|modificar)\b",
    r"(?i)\b(crea|crear|trunca|truncar|concede|conceder|revoca|revocar)\b",
    r"(?i)\b(password|credential|secret|token|api[_-]?key)\b",
]


def validate_question(question: str, config: AppConfig) -> None:
    """Reject obvious out-of-scope requests before they can reach SQL generation."""

    if len(question) > config.query.max_question_length:
        raise ValueError("question_too_long")
    if any(re.search(pattern, question) for pattern in OUT_OF_SCOPE_PATTERNS):
        raise ValueError("out_of_scope_request")


def validate_context(context: dict[str, str] | None, config: AppConfig) -> None:
    """Bound optional user context before it is included in an LLM prompt."""

    if context is None:
        return
    if len(context) > config.query.max_context_items:
        raise ValueError("context_too_many_items")
    if any(len(key) > config.query.max_context_key_length for key in context):
        raise ValueError("context_key_too_long")
    if any(len(value) > config.query.max_context_value_length for value in context.values()):
        raise ValueError("context_value_too_long")


def normalize_rows(rows: list[dict[str, Any]], config: AppConfig) -> list[dict[str, Any]]:
    """Apply output limits, denied-column filtering, and configured redaction."""

    denied = {name.lower() for name in config.data_model.denied_columns}
    normalized: list[dict[str, Any]] = []
    for row in rows[: config.query.absolute_max_rows]:
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if key.lower() in denied:
                continue
            if isinstance(value, str):
                value = value[: config.output.max_cell_length]
                for pattern in config.output.redact_patterns:
                    value = re.sub(pattern, "[REDACTED]", value)
            clean[key] = value
        normalized.append(clean)
    return normalized


def has_scope(scopes: set[str], required_scope: str) -> bool:
    """Check an exact authorization grant without applying implicit inheritance."""

    return required_scope in scopes
