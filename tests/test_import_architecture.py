import ast
import importlib
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _import_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_database_capability_modules_are_importable() -> None:
    modules = [
        "app.capabilities.database.models",
        "app.capabilities.database.security",
        "app.capabilities.database.sql_validator",
    ]

    for module in modules:
        importlib.import_module(module)


def test_database_capability_modules_are_resolvable() -> None:
    modules = [
        "app.capabilities.database.database",
        "app.capabilities.database.llm",
        "app.capabilities.database.models",
        "app.capabilities.database.security",
        "app.capabilities.database.sql_validator",
    ]

    for module in modules:
        assert importlib.util.find_spec(module) is not None


def test_main_imports_database_capability_package() -> None:
    names = _import_names(ROOT / "main.py")

    assert "app.capabilities.database.database" in names
    assert "app.capabilities.database.sql_validator" in names


def test_main_does_not_import_removed_flat_database_modules() -> None:
    names = _import_names(ROOT / "main.py")

    assert names.isdisjoint(
        {
            "app.database",
            "app.llm",
            "app.models",
            "app.security",
            "app.sql_validator",
        }
    )
