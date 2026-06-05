from dataclasses import dataclass

from sqlglot import exp, parse

from app.config import AppConfig


class SqlValidationError(ValueError):
    """Raised when an LLM-generated SQL candidate violates a deterministic rule."""

    pass


@dataclass(frozen=True)
class ValidatedSql:
    sql: str
    relations_used: list[str]


FORBIDDEN_EXPRESSIONS = (
    exp.Alter,
    exp.Cache,
    exp.Command,
    exp.Copy,
    exp.Create,
    exp.Delete,
    exp.Describe,
    exp.Drop,
    exp.Grant,
    exp.Insert,
    exp.Lock,
    exp.Merge,
    exp.Pragma,
    exp.Set,
    exp.Transaction,
    exp.Uncache,
    exp.Update,
    exp.Use,
)


def validate_sql(candidate: str, config: AppConfig, max_rows: int) -> ValidatedSql:
    """Parse, authorize, and bound a read-only query for the configured dialect."""

    try:
        statements = parse(candidate, read=config.database.sqlglot_dialect)
    except Exception as exc:
        raise SqlValidationError("sql_not_parseable") from exc

    if len(statements) != 1:
        raise SqlValidationError("multiple_statements_not_allowed")

    statement = statements[0]
    if not isinstance(statement, exp.Select):
        raise SqlValidationError("read_only_select_required")
    for forbidden in statement.find_all(*FORBIDDEN_EXPRESSIONS):
        raise SqlValidationError(
            f"read_only_expression_required:{forbidden.key or type(forbidden).__name__}"
        )
    if statement.args.get("into"):
        raise SqlValidationError("select_into_not_allowed")

    if list(statement.find_all(exp.Star)):
        raise SqlValidationError("select_star_not_allowed")

    # Build allowlists from the versioned logical data model, not the physical schema.
    allowed_relations = {
        relation.name.lower(): set(column.lower() for column in relation.columns)
        for relation in config.data_model.allowed_relations
    }
    denied_columns = {column.lower() for column in config.data_model.denied_columns}

    # CTE aliases are query-local names and should not be confused with physical tables.
    cte_aliases = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
    relations_used: list[str] = []
    relation_by_qualifier: dict[str, str] = {}
    for table in statement.find_all(exp.Table):
        relation_name = ".".join(part for part in [table.db, table.name] if part).lower()
        if not table.db and relation_name in cte_aliases:
            continue
        if relation_name not in allowed_relations:
            raise SqlValidationError(f"relation_not_allowed:{relation_name}")
        relations_used.append(relation_name)
        relation_by_qualifier[table.alias_or_name.lower()] = relation_name
        relation_by_qualifier[table.name.lower()] = relation_name

    if not relations_used:
        raise SqlValidationError("authorized_relation_required")

    for column in statement.find_all(exp.Column):
        name = column.name.lower()
        if name in denied_columns:
            raise SqlValidationError(f"denied_column:{name}")
        qualifier = column.table.lower()
        if qualifier:
            relation_name = relation_by_qualifier.get(qualifier)
            # Columns selected from a CTE were already authorized in its definition.
            if relation_name is None and qualifier in cte_aliases:
                continue
            if relation_name is None or name not in allowed_relations[relation_name]:
                raise SqlValidationError(f"column_not_allowed:{qualifier}.{name}")
            continue

        physical_relations = sorted(set(relations_used))
        if not all(name in allowed_relations[relation] for relation in physical_relations):
            raise SqlValidationError(f"column_not_allowed:{name}")

    allowed_functions = {
        function.lower() for function in config.data_model.allowed_functions
    }
    for function in statement.find_all(exp.Func):
        function_name = function.sql_name().lower()
        if function_name not in allowed_functions:
            raise SqlValidationError(f"function_not_allowed:{function_name}")

    bounded_rows = min(max_rows, config.query.absolute_max_rows)
    existing_limit = statement.args.get("limit")
    if existing_limit:
        limit_expression = existing_limit.expression
        if not isinstance(limit_expression, exp.Literal) or not limit_expression.is_int:
            raise SqlValidationError("literal_integer_limit_required")
        bounded_rows = min(bounded_rows, int(limit_expression.this))

    # Re-render the parsed AST with a server-enforced upper bound.
    safe_statement = statement.limit(bounded_rows, copy=True)
    return ValidatedSql(
        sql=safe_statement.sql(dialect=config.database.sqlglot_dialect),
        relations_used=sorted(set(relations_used)),
    )
