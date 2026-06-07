from pathlib import Path

import yaml

from app.config import AppConfig


ROOT = Path(__file__).resolve().parents[1]


def load_config() -> AppConfig:
    with open(ROOT / "config/data-agent.yaml", encoding="utf-8") as handle:
        return AppConfig.model_validate(yaml.safe_load(handle))


def test_all_llm_prompts_are_loaded_from_configuration() -> None:
    config = load_config()

    assert "{schema_context}" in config.prompts.sql_generation.system
    assert "{question}" in config.prompts.sql_generation.user


def test_sql_generation_prompt_requires_question_language_for_assumptions() -> None:
    prompt = load_config().prompts.sql_generation.system.lower()

    assert "same language" in prompt
    assert "spanish" not in prompt
    assert "espanol" not in prompt


def test_ask_database_capability_uses_service_identity() -> None:
    capability = load_config().capability("ask_database")

    assert capability.identity_mode == "service"
    assert capability.required_grants == ["data:read"]
    assert capability.sql_viewer_grant == "data:sql:read"
