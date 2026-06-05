import asyncio
import os
from collections.abc import Callable

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.pool import NullPool

from app.config import AppConfig, DatabaseConfig, load_secret
from app.capabilities.database.models import QueryResult


DatabaseSetup = Callable[[Connection, DatabaseConfig], None]


def _configure_postgresql(connection: Connection, config: DatabaseConfig) -> None:
    """Apply PostgreSQL controls that must hold for every query transaction."""

    connection.execute(text("SET TRANSACTION READ ONLY"))
    connection.execute(
        text("SELECT set_config('statement_timeout', :timeout_ms, true)"),
        {"timeout_ms": str(config.statement_timeout_ms)},
    )


# Dialect-specific controls are explicit so unsupported engines fail closed.
DATABASE_SETUPS: dict[str, DatabaseSetup] = {
    "postgresql": _configure_postgresql,
}


def _execute_sync(sql: str, config: AppConfig) -> QueryResult:
    """Execute one validated statement through a short-lived SQLAlchemy engine."""

    secret = load_secret(os.environ["DATABASE_SECRET_ARN"])
    database_uri = secret[config.database.secret_uri_key]
    setup = DATABASE_SETUPS.get(config.database.dialect)
    if setup is None:
        raise RuntimeError(f"unsupported_database_dialect:{config.database.dialect}")

    # NullPool avoids retaining connections or credentials between invocations.
    engine = create_engine(
        database_uri,
        connect_args=config.database.connect_args,
        hide_parameters=True,
        poolclass=NullPool,
    )
    try:
        with engine.connect() as connection:
            with connection.begin():
                setup(connection, config.database)
                # Execute the already validated SQL without SQLAlchemy bind parsing.
                result = connection.exec_driver_sql(sql)
                rows = [dict(row) for row in result.mappings().all()]
        return QueryResult(rows=rows)
    finally:
        engine.dispose()


async def execute_read_only_sql(sql: str, config: AppConfig) -> QueryResult:
    """Run validated SQL without blocking the asynchronous MCP request handler."""

    return await asyncio.to_thread(_execute_sync, sql, config)
