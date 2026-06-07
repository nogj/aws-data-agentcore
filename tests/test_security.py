import yaml
import json
from datetime import datetime
from decimal import Decimal

from app.config import AppConfig
from app.capabilities.database.security import (
    normalize_rows,
    validate_context,
    validate_question,
)
from main import _json_answer, _json_payload, _response_rows


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


def test_marks_response_truncated_when_response_row_cap_omits_rows() -> None:
    app_config = config()
    rows = [{"ci_id": str(index)} for index in range(app_config.output.max_summary_rows + 1)]

    response_rows, truncated = _response_rows(rows, len(rows), app_config)

    assert len(response_rows) == app_config.output.max_summary_rows
    assert truncated


def test_marks_response_truncated_when_normalization_omits_source_rows() -> None:
    app_config = config()
    rows = [{"ci_id": "1"}]

    _response_rows_result, truncated = _response_rows(
        rows, source_row_count=2, config=app_config
    )

    assert truncated


def test_json_answer_serializes_rows_without_llm_summary() -> None:
    payload = _json_payload(
        rows=[{"ci_name": "aplicación", "criticality": "high"}],
        row_count=1,
        truncated=False,
        assumptions=["limited to active CIs"],
    )

    assert payload == {
        "assumptions": ["limited to active CIs"],
        "row_count": 1,
        "rows": [{"ci_name": "aplicación", "criticality": "high"}],
        "truncated": False,
    }
    assert json.loads(_json_answer(payload)) == payload


def test_json_payload_converts_database_scalars_to_json_values() -> None:
    payload = _json_payload(
        rows=[
            {
                "ci_name": "Payments API",
                "modified_at": datetime(2026, 6, 7, 9, 22, 1),
                "score": Decimal("2.5"),
                "count": Decimal("6"),
            }
        ],
        row_count=1,
        truncated=False,
        assumptions=[],
    )

    assert payload["rows"] == [
        {
            "ci_name": "Payments API",
            "modified_at": "2026-06-07T09:22:01",
            "score": 2.5,
            "count": 6,
        }
    ]
    assert json.loads(_json_answer(payload)) == payload
