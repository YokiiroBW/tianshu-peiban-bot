"""Background Memory input settings agree with runtime validation and old overrides."""

import json
from pathlib import Path

import pytest

from tianshu.memory.materials import ConsolidationPolicy
from tianshu.models.budgets import BACKGROUND_INPUT_TOKENS
from tianshu.runtime.config import Settings
from tianshu.runtime.errors import AppError


def example():
    return json.loads((Path(__file__).resolve().parents[2] /
        "deploy/core/settings.example.json").read_text(encoding="utf-8"))


def test_background_default_applies_when_the_deployment_has_no_memory_override(tmp_path):
    document = example()
    document.setdefault("memory", {})["consolidation"] = {}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    loaded = Settings.load(path)
    policy = ConsolidationPolicy.from_settings(loaded.get("memory")["consolidation"])
    assert policy.max_input_tokens == BACKGROUND_INPUT_TOKENS == 200000
    assert policy.max_output_tokens == 4096 and policy.reasoning_reserve_tokens == 8192
    assert (policy.max_episodes, policy.max_subjects, policy.max_materials,
        policy.max_old_records, policy.context_messages, policy.max_source_characters) == (32, 16, 32, 32, 6, 12000)


@pytest.mark.parametrize("tokens", [4000, 128000, 200000])
def test_runtime_accepts_the_new_input_ceiling_and_preserves_explicit_smaller_limits(tmp_path, tokens):
    document = example()
    document.setdefault("memory", {})["consolidation"] = {"max_input_tokens": tokens}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    loaded = Settings.load(path)
    policy = ConsolidationPolicy.from_settings(loaded.get("memory")["consolidation"])
    assert policy.max_input_tokens == tokens
    assert policy.max_output_tokens + policy.reasoning_reserve_tokens == 12288


@pytest.mark.parametrize("tokens, keyword", [(3999, "minimum"), (200001, "maximum")])
def test_runtime_and_memory_reject_the_same_outside_input_bounds(tmp_path, tokens, keyword):
    with pytest.raises(AppError, match="invalid_memory_consolidation_settings"):
        ConsolidationPolicy.from_settings({"max_input_tokens": tokens})
    document = example()
    document.setdefault("memory", {})["consolidation"] = {"max_input_tokens": tokens}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_configuration:memory.consolidation.max_input_tokens:" + keyword):
        Settings.load(path)
