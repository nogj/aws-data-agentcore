from pathlib import Path
from typing import Any

import app.database as database
import yaml
from app.config import AppConfig
from app.models import QueryResult


ROOT = Path(__file__).resolve().parents[1]


def config() -> AppConfig:
    with open(ROOT / "config/data-agent.yaml", encoding="utf-8") as handle:
        return AppConfig.model_validate(yaml.safe_load(handle))


class FakeResult:
    def mappings(self) -> "FakeResult":
        return self

    def all(self) -> list[dict[str, Any]]:
        return [{"item_id": "123"}]


class FakeTransaction:
    def __enter__(self) -> "FakeTransaction":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def begin(self) -> FakeTransaction:
        return FakeTransaction()

    def execute(self, statement: Any, _parameters: Any = None) -> FakeResult:
        self.statements.append(str(statement))
        return FakeResult()

    def exec_driver_sql(self, statement: str) -> FakeResult:
        self.statements.append(statement)
        return FakeResult()


class FakeEngine:
    def __init__(self) -> None:
        self.connection = FakeConnection()
        self.disposed = False

    def connect(self) -> FakeConnection:
        return self.connection

    def dispose(self) -> None:
        self.disposed = True


def test_executes_query_with_postgresql_read_only_controls(monkeypatch: Any) -> None:
    engine = FakeEngine()
    monkeypatch.setenv("DATABASE_SECRET_ARN", "secret-arn")
    monkeypatch.setattr(database, "load_secret", lambda _arn: {"database_uri": "uri"})
    monkeypatch.setattr(database, "create_engine", lambda *_args, **_kwargs: engine)

    result = database._execute_sync("SELECT item_id FROM inventory.items", config())

    assert result == QueryResult(rows=[{"item_id": "123"}])
    assert engine.connection.statements == [
        "SET TRANSACTION READ ONLY",
        "SELECT set_config('statement_timeout', :timeout_ms, true)",
        "SELECT item_id FROM inventory.items",
    ]
    assert engine.disposed


def test_rejects_dialect_without_security_adapter(monkeypatch: Any) -> None:
    app_config = config()
    app_config.database.dialect = "unsupported"
    monkeypatch.setenv("DATABASE_SECRET_ARN", "secret-arn")
    monkeypatch.setattr(database, "load_secret", lambda _arn: {"database_uri": "uri"})

    try:
        database._execute_sync("SELECT 1", app_config)
    except RuntimeError as exc:
        assert str(exc) == "unsupported_database_dialect:unsupported"
        return
    raise AssertionError("Expected RuntimeError")
