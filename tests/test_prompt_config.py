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
    assert "{truncated}" in config.prompts.result_summary.user


def test_summary_prompt_requires_question_language() -> None:
    prompt = load_config().prompts.result_summary.system.lower()

    assert "same language" in prompt
    assert "spanish" not in prompt
    assert "espanol" not in prompt
