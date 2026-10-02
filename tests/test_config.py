import os
import pytest
from src.config import resolve_env_placeholders, load_router_config, AppSettings


def test_resolve_env_placeholders_with_set_and_unset_vars(monkeypatch):
    monkeypatch.setenv("TEST_BASE_URL", "http://127.0.0.1:20128/v1")
    monkeypatch.setenv("TEST_MODEL", "ag/claude-opus-4-6-thinking")
    
    raw = {
        "url": "${TEST_BASE_URL}",
        "model": "${TEST_MODEL}",
        "missing": "${UNSET_VAR_PLACEHOLDER}",
        "nested": {
            "text": "prefix_${TEST_MODEL}_suffix"
        }
    }
    
    resolved, unresolved = resolve_env_placeholders(raw)
    assert resolved["url"] == "http://127.0.0.1:20128/v1"
    assert resolved["model"] == "ag/claude-opus-4-6-thinking"
    assert resolved["missing"] == "${UNSET_VAR_PLACEHOLDER}"
    assert resolved["nested"]["text"] == "prefix_ag/claude-opus-4-6-thinking_suffix"
    assert "UNSET_VAR_PLACEHOLDER" in unresolved


def test_app_settings_defaults(monkeypatch):
    # Ensure clean env without overrides
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("ROUTER_PORT", raising=False)
    monkeypatch.delenv("UPSTREAM_BASE_URL", raising=False)
    
    settings = AppSettings()
    assert settings.port == 20250  # Must default to 20250 to avoid collision with 20200
    assert settings.host == "127.0.0.1"
    assert settings.upstream_base_url == "http://127.0.0.1:20128/v1"


def test_load_router_config_from_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTIGRAVITY_BASE_URL", "http://127.0.0.1:20128/v1")
    monkeypatch.setenv("OPUS_4_6_MODEL_ID", "ag/claude-opus-4-6-thinking")
    monkeypatch.setenv("PRO_RPM", "60")
    monkeypatch.setenv("PRO_TPM", "1000000")
    
    yaml_content = """
version: "2026.1"
providers:
  antigravity:
    base_url: "${ANTIGRAVITY_BASE_URL}"
    api_key: "${ANTIGRAVITY_API_KEY}"
models:
  opus_apex:
    provider: antigravity
    model: "${OPUS_4_6_MODEL_ID}"
    concurrency_limit: 5
    timeout_seconds: 180
    parameters:
      max_tokens: 48000
      thinking:
        type: enabled
        budget_tokens: 32768
resilience:
  fallback_chain:
    opus_apex: sonnet_fallback
router:
  routing_strategy: rule_based
  rules:
    - name: rescue_opus
      priority: 100
      condition: "context.metadata.retry_count >= 4"
      target: opus_apex
circuit_breakers:
  identical_error_loop:
    consecutive: 3
    action: force_escalate_one_tier
"""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml_content, encoding="utf-8")
    
    cfg = load_router_config(str(cfg_file))
    assert cfg.version == "2026.1"
    assert cfg.providers["antigravity"].base_url == "http://127.0.0.1:20128/v1"
    assert cfg.models["opus_apex"].model == "ag/claude-opus-4-6-thinking"
    assert cfg.models["opus_apex"].parameters.max_tokens == 48000
    assert cfg.models["opus_apex"].parameters.thinking.budget_tokens == 32768
    assert cfg.router.rules[0].name == "rescue_opus"
    assert cfg.router.rules[0].priority == 100
    assert cfg.circuit_breakers.identical_error_loop.consecutive == 3
