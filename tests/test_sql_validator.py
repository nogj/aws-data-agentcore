import yaml
import pytest

from app.config import AppConfig
from app.capabilities.database.sql_validator import SqlValidationError, validate_sql


def config() -> AppConfig:
    with open("config/data-agent.yaml", encoding="utf-8") as handle:
        return AppConfig.model_validate(yaml.safe_load(handle))


def relation_name() -> str:
    return config().data_model.allowed_relations[0].name


def relation_columns() -> tuple[str, str]:
    columns = config().data_model.allowed_relations[0].columns
    return columns[0], columns[1]


def test_accepts_and_bounds_authorized_select() -> None:
    relation = relation_name()
    first_column, second_column = relation_columns()
    result = validate_sql(
        f"SELECT {first_column}, {second_column} FROM {relation} LIMIT 999",
        config(),
        max_rows=25,
    )
    assert "LIMIT 25" in result.sql
    assert result.relations_used == [relation]


def test_rejects_write() -> None:
    relation = relation_name()
    try:
        validate_sql(f"DELETE FROM {relation}", config(), max_rows=10)
    except SqlValidationError:
        return
    raise AssertionError("Expected SqlValidationError")


def test_rejects_unknown_relation() -> None:
    try:
        validate_sql("SELECT id FROM public.users", config(), max_rows=10)
    except SqlValidationError:
        return
    raise AssertionError("Expected SqlValidationError")


def test_rejects_select_star() -> None:
    relation = relation_name()
    try:
        validate_sql(f"SELECT * FROM {relation}", config(), max_rows=10)
    except SqlValidationError:
        return
    raise AssertionError("Expected SqlValidationError")


def test_accepts_cte_over_authorized_relation() -> None:
    relation = relation_name()
    first_column, _ = relation_columns()
    result = validate_sql(
        f"WITH filtered AS (SELECT {first_column} FROM {relation}) "
        f"SELECT {first_column} FROM filtered",
        config(),
        max_rows=10,
    )
    assert result.relations_used == [relation]


def test_accepts_cte_projected_aggregate_alias() -> None:
    relation = relation_name()
    first_column, _ = relation_columns()
    result = validate_sql(
        f"WITH totals AS (SELECT COUNT({first_column}) AS total FROM {relation}) "
        "SELECT total FROM totals",
        config(),
        max_rows=10,
    )
    assert result.relations_used == [relation]


def test_rejects_unknown_qualified_cte_column() -> None:
    relation = relation_name()
    first_column, _ = relation_columns()
    try:
        validate_sql(
            f"WITH filtered AS (SELECT {first_column} FROM {relation}) "
            "SELECT filtered.not_projected FROM filtered",
            config(),
            max_rows=10,
        )
    except SqlValidationError as exc:
        assert "column_not_allowed:filtered.not_projected" in str(exc)
        return
    raise AssertionError("Expected SqlValidationError")


def test_rejects_unreferenced_cte_projected_alias() -> None:
    relation = relation_name()
    first_column, _ = relation_columns()
    try:
        validate_sql(
            f"WITH totals AS (SELECT COUNT({first_column}) AS total FROM {relation}) "
            f"SELECT total FROM {relation}",
            config(),
            max_rows=10,
        )
    except SqlValidationError as exc:
        assert "column_not_allowed:total" in str(exc)
        return
    raise AssertionError("Expected SqlValidationError")


def test_accepts_derived_table_projected_alias() -> None:
    relation = relation_name()
    first_column, _ = relation_columns()
    result = validate_sql(
        f"SELECT total FROM (SELECT COUNT({first_column}) AS total FROM {relation}) "
        "AS derived",
        config(),
        max_rows=10,
    )
    assert result.relations_used == [relation]


def test_rejects_write_inside_cte() -> None:
    relation = relation_name()
    first_column, _ = relation_columns()
    try:
        validate_sql(
            f"WITH deleted AS (DELETE FROM {relation} RETURNING {first_column}) "
            f"SELECT {first_column} FROM deleted",
            config(),
            max_rows=10,
        )
    except SqlValidationError as exc:
        assert "read_only_expression_required" in str(exc)
        return
    raise AssertionError("Expected SqlValidationError")


def test_rejects_locking_select() -> None:
    relation = relation_name()
    first_column, _ = relation_columns()
    try:
        validate_sql(
            f"SELECT {first_column} FROM {relation} FOR UPDATE",
            config(),
            max_rows=10,
        )
    except SqlValidationError as exc:
        assert "read_only_expression_required" in str(exc)
        return
    raise AssertionError("Expected SqlValidationError")


def test_accepts_configured_function() -> None:
    relation = relation_name()
    first_column, _ = relation_columns()
    result = validate_sql(
        f"SELECT COUNT({first_column}) FROM {relation}",
        config(),
        max_rows=10,
    )
    assert result.relations_used == [relation]


def test_accepts_logical_connectors_in_where() -> None:
    relation = relation_name()
    first_column, second_column = relation_columns()
    result = validate_sql(
        f"SELECT {first_column} FROM {relation} "
        f"WHERE {first_column} IS NOT NULL AND {second_column} IS NOT NULL",
        config(),
        max_rows=10,
    )
    assert result.relations_used == [relation]


def test_rejects_unconfigured_function() -> None:
    relation = relation_name()
    first_column, _ = relation_columns()
    try:
        validate_sql(
            f"SELECT LOWER({first_column}) FROM {relation}",
            config(),
            max_rows=10,
        )
    except SqlValidationError:
        return
    raise AssertionError("Expected SqlValidationError")


def test_rejects_column_not_authorized_for_qualified_relation() -> None:
    app_config = config()
    first_relation = app_config.data_model.allowed_relations[0]
    second_relation = app_config.data_model.allowed_relations[1]
    first_only_column = next(
        column for column in first_relation.columns if column not in second_relation.columns
    )
    try:
        validate_sql(
            f"SELECT r.{first_only_column} FROM {second_relation.name} AS r",
            app_config,
            max_rows=10,
        )
    except SqlValidationError:
        return
    raise AssertionError("Expected SqlValidationError")


@pytest.mark.parametrize(
    ("sql_template", "expected_reason"),
    [
        ("SELECT {column} FROM catalog.{relation}", "catalog_not_allowed"),
        (
            "SELECT {column} FROM {relation} TABLESAMPLE SYSTEM (1)",
            "read_only_expression_required",
        ),
        (
            "SELECT {column} FROM {relation}, LATERAL (SELECT {column}) AS x",
            "read_only_expression_required",
        ),
        (
            "SELECT x FROM UNNEST(ARRAY[1, 2]) AS t(x)",
            "read_only_expression_required",
        ),
    ],
)
def test_rejects_unsupported_read_constructs(
    sql_template: str, expected_reason: str
) -> None:
    relation = relation_name()
    first_column, _ = relation_columns()
    try:
        validate_sql(
            sql_template.format(relation=relation, column=first_column),
            config(),
            max_rows=10,
        )
    except SqlValidationError as exc:
        assert expected_reason in str(exc)
        return
    raise AssertionError("Expected SqlValidationError")
