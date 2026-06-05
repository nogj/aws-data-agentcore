import yaml

from app.config import AppConfig
from app.capabilities.database.security import (
    normalize_rows,
    validate_context,
    validate_question,
)


def config() -> AppConfig:
    with open("config/data-agent.yaml", encoding="utf-8") as handle:
        return AppConfig.model_validate(yaml.safe_load(handle))


def test_rejects_write_intent() -> None:
    try:
        validate_question("Borra los servidores retirados", config())
    except ValueError:
        return
    raise AssertionError("Expected ValueError")


def test_removes_denied_columns_and_redacts_values() -> None:
    rows = normalize_rows(
        [
            {
                "ci_name": "app",
                "password": "secret",
                "notes": "postgresql://user:pass@internal.invalid/db",
            }
        ],
        config(),
    )
    assert "password" not in rows[0]
    assert rows[0]["notes"] == "[REDACTED]"


def test_rejects_oversized_context_value() -> None:
    try:
        validate_context(
            {"filter": "x" * (config().query.max_context_value_length + 1)},
            config(),
        )
    except ValueError:
        return
    raise AssertionError("Expected ValueError")
