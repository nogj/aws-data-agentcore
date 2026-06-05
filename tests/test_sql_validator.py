import yaml

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


def test_accepts_configured_function() -> None:
    relation = relation_name()
    first_column, _ = relation_columns()
    result = validate_sql(
        f"SELECT COUNT({first_column}) FROM {relation}",
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
