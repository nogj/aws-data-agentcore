import yaml

from app.config import AppConfig
from app.capabilities.database.security import (
    normalize_rows,
    validate_context,
    validate_question,
)
from main import _summary_input


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


def test_marks_summary_truncated_when_summary_row_cap_omits_rows() -> None:
    app_config = config()
    rows = [{"ci_id": str(index)} for index in range(app_config.output.max_summary_rows + 1)]

    summary_rows, truncated = _summary_input(rows, len(rows), app_config)

    assert len(summary_rows) == app_config.output.max_summary_rows
    assert truncated


def test_marks_summary_truncated_when_normalization_omits_source_rows() -> None:
    app_config = config()
    rows = [{"ci_id": "1"}]

    _summary_rows, truncated = _summary_input(rows, source_row_count=2, config=app_config)

    assert truncated
