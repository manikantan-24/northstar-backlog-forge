"""
Comprehensive tests for all new enterprise modules added in the production-grade pass.

Covers:
  - feature_flags.FeatureFlags  (load, get, save, apply_stage_locks)
  - rate_limiter                (check_rate_limit, get_usage_summary)
  - startup_check              (check_required_secrets, get_configured_integrations)
  - ollama_manager             (is_running, ensure_running no-op when binary missing)
  - story_writer_agent         (_repair_source_topic_id)
  - audit_log                  (hash chain: record, verify_chain, chain_fingerprint)
  - orchestrator               (_build_jira_tool, _build_github_tool, _build_confluence_tool)
  - guardrails                 (dangling_topic_ref is warn, not error)
  - prompt caching             (cache_control present in ClaudeTool)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ══════════════════════════════════════════════════════════════════════════════
# feature_flags
# ══════════════════════════════════════════════════════════════════════════════

class TestFeatureFlags:
    def _make_flags(self, yaml_text: str):
        import yaml
        from feature_flags import FeatureFlags, FLAGS_PATH
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_text)
            tmp_path = Path(f.name)
        old = FLAGS_PATH
        import feature_flags as ff_mod
        ff_mod.FLAGS_PATH = tmp_path
        try:
            flags = FeatureFlags.load()
        finally:
            ff_mod.FLAGS_PATH = old
            tmp_path.unlink(missing_ok=True)
        return flags

    def test_defaults_are_loaded_when_no_yaml(self, tmp_path):
        from feature_flags import FeatureFlags, FLAGS_PATH
        import feature_flags as ff_mod
        ff_mod.FLAGS_PATH = tmp_path / "nonexistent.yaml"
        try:
            ff = FeatureFlags.load()
            assert ff.allowed_presets("contributor") == ["free", "balanced"]
            assert ff.is_enabled("contributor", "jira_write_back") is True
            assert ff.is_enabled("viewer", "jira_write_back") is False
        finally:
            ff_mod.FLAGS_PATH = FLAGS_PATH

    def test_admin_always_has_full_access(self, tmp_path):
        from feature_flags import FeatureFlags, FLAGS_PATH
        import feature_flags as ff_mod
        ff_mod.FLAGS_PATH = tmp_path / "nonexistent.yaml"
        try:
            ff = FeatureFlags.load()
            assert "premium" in ff.allowed_presets("admin")
            assert "local" in ff.allowed_presets("admin")
            assert ff.is_enabled("admin", "pii_override") is True
            assert ff.is_enabled("admin", "live_jira_read") is True
        finally:
            ff_mod.FLAGS_PATH = FLAGS_PATH

    def test_yaml_overrides_contributor_presets(self):
        ff = self._make_flags(
            "contributor:\n  allowed_presets:\n    - free\n    - balanced\n    - premium\n"
        )
        assert "premium" in ff.allowed_presets("contributor")

    def test_stage_lock_returns_none_when_not_locked(self, tmp_path):
        from feature_flags import FeatureFlags, FLAGS_PATH
        import feature_flags as ff_mod
        ff_mod.FLAGS_PATH = tmp_path / "nonexistent.yaml"
        try:
            ff = FeatureFlags.load()
            assert ff.stage_lock("contributor", "story_writer") is None
        finally:
            ff_mod.FLAGS_PATH = FLAGS_PATH

    def test_stage_lock_returns_model_when_locked(self):
        ff = self._make_flags(
            "contributor:\n  stage_model_locks:\n    story_writer: claude-sonnet-4-5\n"
        )
        assert ff.stage_lock("contributor", "story_writer") == "claude-sonnet-4-5"
        assert ff.stage_lock("contributor", "parser") is None

    def test_apply_stage_locks_overrides_matching_stages(self):
        ff = self._make_flags(
            "contributor:\n  stage_model_locks:\n    story_writer: claude-haiku-4-5\n"
        )
        models = {
            "parser": "gemini-2.5-flash",
            "story_writer": "gemini-2.5-flash",
            "gap_detector": "gemini-2.5-flash",
        }
        result = ff.apply_stage_locks("contributor", models)
        assert result["story_writer"] == "claude-haiku-4-5"
        assert result["parser"] == "gemini-2.5-flash"  # unchanged

    def test_save_and_reload_round_trips(self, tmp_path):
        from feature_flags import FeatureFlags, FLAGS_PATH
        import feature_flags as ff_mod
        save_path = tmp_path / "flags.yaml"
        ff_mod.FLAGS_PATH = save_path
        try:
            ff = FeatureFlags.load()
            d = ff.to_dict()
            d["contributor"]["compare_mode"] = False
            FeatureFlags.save(d)
            ff2 = FeatureFlags.load()
            assert ff2.is_enabled("contributor", "compare_mode") is False
        finally:
            ff_mod.FLAGS_PATH = FLAGS_PATH

    def test_viewer_has_no_allowed_presets(self, tmp_path):
        from feature_flags import FeatureFlags, FLAGS_PATH
        import feature_flags as ff_mod
        ff_mod.FLAGS_PATH = tmp_path / "nonexistent.yaml"
        try:
            ff = FeatureFlags.load()
            assert ff.allowed_presets("viewer") == []
            assert ff.max_runs_per_hour("viewer") == 0
        finally:
            ff_mod.FLAGS_PATH = FLAGS_PATH


# ══════════════════════════════════════════════════════════════════════════════
# rate_limiter
# ══════════════════════════════════════════════════════════════════════════════

class TestRateLimiter:
    def _write_run(self, runs_dir: Path, user_id: str, timestamp: str, cost: float):
        user_dir = runs_dir / user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / f"{timestamp}.json").write_text(
            json.dumps({"timestamp": timestamp, "user_id": user_id, "cost_usd": cost})
        )

    def test_no_runs_passes(self, tmp_path, monkeypatch):
        import rate_limiter as rl
        monkeypatch.setattr(rl, "_RUNS_DIR", tmp_path)
        rl.check_rate_limit("alice")  # should not raise

    def test_exceeding_hourly_cap_raises(self, tmp_path, monkeypatch):
        import rate_limiter as rl
        from datetime import datetime, timezone
        monkeypatch.setattr(rl, "_RUNS_DIR", tmp_path)
        monkeypatch.setattr(rl, "_MAX_RUNS_PER_HOUR", 2)
        now = datetime.now(timezone.utc)
        # Write 3 runs with unique timestamps within the last hour
        for i in range(3):
            ts = now.strftime(f"%Y%m%d_%H%M{i:02d}")
            self._write_run(tmp_path, "alice", ts, 0.01)
        with pytest.raises(rl.RateLimitError, match="Rate limit"):
            rl.check_rate_limit("alice")

    def test_exceeding_daily_cost_raises(self, tmp_path, monkeypatch):
        import rate_limiter as rl
        from datetime import datetime, timezone
        monkeypatch.setattr(rl, "_RUNS_DIR", tmp_path)
        monkeypatch.setattr(rl, "_MAX_COST_PER_DAY", 1.0)
        now = datetime.now(timezone.utc)
        user_dir = tmp_path / "alice"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "run.json").write_text(
            json.dumps({"timestamp": now.strftime("%Y%m%d_%H%M%S"),
                        "user_id": "alice", "cost_usd": 2.0})
        )
        with pytest.raises(rl.RateLimitError, match="cost ceiling"):
            rl.check_rate_limit("alice")

    def test_get_usage_summary_returns_expected_keys(self, tmp_path, monkeypatch):
        import rate_limiter as rl
        monkeypatch.setattr(rl, "_RUNS_DIR", tmp_path)
        summary = rl.get_usage_summary("bob")
        assert "runs_last_hour" in summary
        assert "max_runs_per_hour" in summary
        assert "cost_today_usd" in summary
        assert "max_cost_per_day_usd" in summary
        assert summary["runs_last_hour"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# startup_check
# ══════════════════════════════════════════════════════════════════════════════

class TestStartupCheck:
    def test_raises_when_anthropic_key_missing(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from startup_check import check_required_secrets
        # Message says "Anthropic API key" (human readable), not the var name
        with pytest.raises(RuntimeError, match="Anthropic"):
            check_required_secrets()

    def test_passes_when_anthropic_key_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        from startup_check import check_required_secrets
        warnings = check_required_secrets()
        assert isinstance(warnings, list)

    def test_partial_jira_config_returns_warning(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
        monkeypatch.delenv("JIRA_EMAIL", raising=False)
        monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
        from startup_check import check_required_secrets
        warnings = check_required_secrets()
        assert any("Jira" in w or "JIRA" in w for w in warnings)

    def test_get_configured_integrations_returns_all_keys(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        from startup_check import get_configured_integrations
        result = get_configured_integrations()
        assert "anthropic" in result
        assert "jira" in result
        assert "github" in result
        assert "atlassian_mcp" in result
        assert "github_mcp" in result

    def test_atlassian_mcp_flag_detected(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("ATLASSIAN_MCP_ENABLED", "1")
        from startup_check import get_configured_integrations
        assert get_configured_integrations()["atlassian_mcp"] is True

    def test_atlassian_mcp_false_when_not_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.delenv("ATLASSIAN_MCP_ENABLED", raising=False)
        from startup_check import get_configured_integrations
        assert get_configured_integrations()["atlassian_mcp"] is False


# ══════════════════════════════════════════════════════════════════════════════
# ollama_manager
# ══════════════════════════════════════════════════════════════════════════════

class TestOllamaManager:
    def test_is_running_returns_false_when_unreachable(self):
        import ollama_manager as om
        # ollama_manager does lazy `import requests` inside functions — patch at source
        with patch("requests.get", side_effect=Exception("connection refused")):
            assert om.is_running() is False

    def test_is_running_returns_true_when_api_responds(self):
        import ollama_manager as om
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.get", return_value=mock_resp):
            assert om.is_running() is True

    def test_ensure_running_returns_true_immediately_when_already_up(self):
        import ollama_manager as om
        with patch.object(om, "is_running", return_value=True):
            ok, msg = om.ensure_running()
            assert ok is True
            assert "already" in msg.lower()

    def test_ensure_running_fails_gracefully_when_binary_missing(self):
        import ollama_manager as om
        with patch.object(om, "is_running", return_value=False), \
             patch("ollama_manager.shutil.which", return_value=None):
            ok, msg = om.ensure_running()
            assert ok is False
            assert "not found" in msg.lower() or "binary" in msg.lower()

    def test_list_models_returns_list(self):
        import ollama_manager as om
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": [{"name": "llama3.2:3b"}]}
        with patch("requests.get", return_value=mock_resp):
            models = om.list_models()
            assert "llama3.2:3b" in models

    def test_list_models_returns_empty_on_error(self):
        import ollama_manager as om
        with patch("requests.get", side_effect=Exception("error")):
            assert om.list_models() == []


# ══════════════════════════════════════════════════════════════════════════════
# story_writer_agent._repair_source_topic_id
# ══════════════════════════════════════════════════════════════════════════════

class TestStoryWriterRepair:
    TOPICS = [
        {"id": "T-01", "theme": "offline POS", "summary": "cash sales during WAN outage",
         "raw_quote": "we lose sales when internet drops"},
        {"id": "T-02", "theme": "loyalty points", "summary": "points not updating after purchase",
         "raw_quote": "my loyalty points never show up"},
    ]
    TOPICS_BY_ID = {t["id"]: t for t in TOPICS}

    def _repair(self, story: dict) -> dict:
        from agents.story_writer_agent import StoryWriterAgent
        StoryWriterAgent._repair_source_topic_id(story, self.TOPICS, self.TOPICS_BY_ID)
        return story

    def test_valid_id_unchanged(self):
        story = {"source_topic_id": "T-01", "title": "anything"}
        assert self._repair(story)["source_topic_id"] == "T-01"

    def test_placeholder_dots_repaired(self):
        story = {
            "source_topic_id": "...",
            "title": "Enable POS Cash Sales during WAN Outage",
            "description": "offline cash sale processing WAN outages",
        }
        result = self._repair(story)
        assert result["source_topic_id"] == "T-01"

    def test_empty_string_repaired(self):
        story = {
            "source_topic_id": "",
            "title": "Loyalty points balance not updating",
            "description": "loyalty points purchase",
        }
        result = self._repair(story)
        assert result["source_topic_id"] == "T-02"

    def test_unknown_id_repaired_by_best_match(self):
        story = {
            "source_topic_id": "T-99",
            "title": "Fix loyalty points after online purchase",
            "description": "loyalty points not showing",
        }
        result = self._repair(story)
        assert result["source_topic_id"] == "T-02"

    def test_null_string_repaired(self):
        story = {
            "source_topic_id": "null",
            "title": "POS offline cash sales WAN outage",
            "description": "internet drops lose sales offline",
        }
        result = self._repair(story)
        assert result["source_topic_id"] == "T-01"

    def test_no_topics_leaves_story_unchanged(self):
        story = {"source_topic_id": "...", "title": "anything"}
        from agents.story_writer_agent import StoryWriterAgent
        StoryWriterAgent._repair_source_topic_id(story, [], {})
        assert story["source_topic_id"] == "..."  # nothing to repair against


# ══════════════════════════════════════════════════════════════════════════════
# audit_log hash chain
# ══════════════════════════════════════════════════════════════════════════════

class TestAuditHashChain:
    def test_chain_fingerprint_changes_with_each_event(self, tmp_path, monkeypatch):
        from memory.audit_log import AuditLog
        monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
        log = AuditLog(run_id="test-001")
        fp0 = log.chain_fingerprint
        log.record("agent1", "event1", {"x": 1})
        fp1 = log.chain_fingerprint
        log.record("agent1", "event2", {"y": 2})
        fp2 = log.chain_fingerprint
        assert fp0 != fp1 != fp2

    def test_verify_chain_passes_on_intact_log(self, tmp_path, monkeypatch):
        from memory.audit_log import AuditLog
        monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
        log = AuditLog(run_id="test-002")
        for i in range(5):
            log.record("agent", f"event_{i}", {"i": i}, reasoning=f"step {i}")
        ok, msg = log.verify_chain()
        assert ok is True
        assert "5" in msg

    def test_verify_chain_detects_tampering(self, tmp_path, monkeypatch):
        import sqlite3
        from memory.audit_log import AuditLog
        db_path = tmp_path / "audit.db"
        monkeypatch.setenv("AUDIT_DB_PATH", str(db_path))
        log = AuditLog(run_id="test-003")
        log.record("agent", "event_a", {"x": 1})
        log.record("agent", "event_b", {"x": 2})
        # Tamper: directly update the event content in SQLite
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE audit_events SET reasoning = 'tampered' WHERE seq = 1")
        conn.commit()
        conn.close()
        ok, msg = log.verify_chain()
        assert ok is False
        assert "mismatch" in msg.lower() or "modified" in msg.lower()

    def test_empty_log_verifies_ok(self, tmp_path, monkeypatch):
        from memory.audit_log import AuditLog
        monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
        log = AuditLog(run_id="test-004")
        ok, msg = log.verify_chain()
        assert ok is True

    def test_chain_fingerprint_is_hex_string(self, tmp_path, monkeypatch):
        from memory.audit_log import AuditLog
        monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
        log = AuditLog(run_id="test-005")
        log.record("a", "b")
        fp = log.chain_fingerprint
        assert len(fp) == 64  # SHA-256 hex
        int(fp, 16)  # must be valid hex


# ══════════════════════════════════════════════════════════════════════════════
# orchestrator tool selection
# ══════════════════════════════════════════════════════════════════════════════

class TestOrchestratorToolSelection:
    def test_github_tool_is_mcp_when_env_set(self, monkeypatch):
        monkeypatch.setenv("GITHUB_MCP_ENABLED", "1")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake")
        monkeypatch.setenv("GITHUB_OWNER", "testorg")
        monkeypatch.setenv("GITHUB_REPO", "testrepo")
        from orchestrator import _build_github_tool
        from tools.mcp_github_tool import MCPGithubTool
        tool = _build_github_tool()
        assert isinstance(tool, MCPGithubTool)
        assert tool._use_mcp is True

    def test_github_tool_is_fixture_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("GITHUB_MCP_ENABLED", raising=False)
        from orchestrator import _build_github_tool
        from tools.github_tool import GithubTool
        tool = _build_github_tool()
        assert type(tool) is GithubTool

    def test_jira_tool_is_mcp_when_env_set(self, monkeypatch):
        monkeypatch.setenv("ATLASSIAN_MCP_ENABLED", "1")
        from orchestrator import _build_jira_tool
        from tools.mcp_atlassian_tool import MCPJiraTool
        tool = _build_jira_tool()
        assert isinstance(tool, MCPJiraTool)
        assert tool._use_mcp is True

    def test_jira_tool_is_rest_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("ATLASSIAN_MCP_ENABLED", raising=False)
        from orchestrator import _build_jira_tool
        from tools.jira_tool import JiraTool
        from tools.mcp_atlassian_tool import MCPJiraTool
        tool = _build_jira_tool()
        assert type(tool) is JiraTool  # not MCP subclass

    def test_confluence_tool_is_mcp_when_env_set(self, monkeypatch):
        monkeypatch.setenv("ATLASSIAN_MCP_ENABLED", "1")
        from orchestrator import _build_confluence_tool
        from tools.mcp_atlassian_tool import MCPConfluenceTool
        tool = _build_confluence_tool()
        assert isinstance(tool, MCPConfluenceTool)

    def test_confluence_tool_is_rest_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("ATLASSIAN_MCP_ENABLED", raising=False)
        from orchestrator import _build_confluence_tool
        from tools.confluence_tool import ConfluenceTool
        from tools.mcp_atlassian_tool import MCPConfluenceTool
        tool = _build_confluence_tool()
        assert type(tool) is ConfluenceTool


# ══════════════════════════════════════════════════════════════════════════════
# prompt caching in ClaudeTool
# ══════════════════════════════════════════════════════════════════════════════

class TestPromptCaching:
    def test_cache_control_present_in_claude_tool_call(self, monkeypatch):
        """System prompt is sent as a list with cache_control when model is claude-*."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key")
        captured = {}

        def fake_create(**kwargs):
            captured["system"] = kwargs.get("system")
            raise Exception("stop after capture")

        from tools.claude_tool import ClaudeTool
        tool = ClaudeTool.__new__(ClaudeTool)
        tool.model = "claude-sonnet-4-5"
        tool.system_prompt = "You are a helpful assistant."

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = fake_create
        tool._client = mock_client

        with pytest.raises(Exception, match="stop after capture"):
            tool._call_with_retry("test message", 100)

        system = captured["system"]
        assert isinstance(system, list), "system should be a list for cache_control"
        assert system[0]["type"] == "text"
        assert "cache_control" in system[0]
        assert system[0]["cache_control"]["type"] == "ephemeral"

    def test_no_cache_control_for_non_claude_model(self, monkeypatch):
        """Non-Claude models (gemini, ollama) get plain string system prompt."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key")
        captured = {}

        def fake_create(**kwargs):
            captured["system"] = kwargs.get("system")
            raise Exception("stop")

        from tools.claude_tool import ClaudeTool
        tool = ClaudeTool.__new__(ClaudeTool)
        tool.model = "gemini-2.5-flash"
        tool.system_prompt = "You are helpful."
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = fake_create
        tool._client = mock_client

        with pytest.raises(Exception, match="stop"):
            tool._call_with_retry("test", 100)

        assert isinstance(captured["system"], str)


# ══════════════════════════════════════════════════════════════════════════════
# MCP tool fallback behaviour (no live calls)
# ══════════════════════════════════════════════════════════════════════════════

class TestMCPToolFallback:
    def test_mcp_jira_falls_back_to_rest_on_failure(self, monkeypatch):
        monkeypatch.setenv("ATLASSIAN_MCP_ENABLED", "1")
        from tools.mcp_atlassian_tool import MCPJiraTool
        tool = MCPJiraTool()
        tool._use_mcp = True
        # Simulate MCP failure
        with patch.object(tool, "_mcp_search", side_effect=Exception("mcp down")):
            # Should fall back to parent JiraTool.list_all() (mock mode)
            result = tool.list_all()
            assert isinstance(result, list)
            assert tool._use_mcp is False  # disabled after failure

    def test_mcp_github_falls_back_to_fixture_on_failure(self, monkeypatch):
        monkeypatch.setenv("GITHUB_MCP_ENABLED", "1")
        from tools.mcp_github_tool import MCPGithubTool
        tool = MCPGithubTool()
        tool._use_mcp = True
        tool._owner = "org"
        tool._repo = "repo"
        with patch("tools.mcp_github_tool._call_github_mcp", side_effect=Exception("no network")):
            result = tool.list_all()
            assert isinstance(result, list)
            assert tool._use_mcp is False

    def test_mcp_confluence_falls_back_to_fixture_on_failure(self, monkeypatch, tmp_path):
        fixture = tmp_path / "page.md"
        fixture.write_text("# Test page\ncontent here")
        monkeypatch.setenv("ATLASSIAN_MCP_ENABLED", "1")
        from tools.mcp_atlassian_tool import MCPConfluenceTool
        tool = MCPConfluenceTool(default_page_path=fixture)
        tool._use_mcp = True
        with patch("tools.mcp_atlassian_tool._call_mcp_tool", side_effect=Exception("mcp down")):
            result = tool.get_page("12345")
            assert "Test page" in result
            assert tool._use_mcp is False


# ══════════════════════════════════════════════════════════════════════════════
# guardrails: severity update verification
# ══════════════════════════════════════════════════════════════════════════════

class TestGuardrailSeverities:
    def test_dangling_topic_ref_is_warn_not_error(self):
        from guardrails import run_guardrails
        synthesis = {
            "topics": [{"id": "T-01", "theme": "test"}],
            "epics": [{"id": "EP-01", "title": "Epic", "stories": [{
                "id": "ST-01", "title": "Story",
                "source_topic_id": "T-DOES-NOT-EXIST",
                "acceptance_criteria": ["Given x, when y, then z.", "Given a, when b, then c."],
                "priority": "Medium", "priority_rationale": "Important for ops.",
                "tags": ["pos"], "evidence": [{"raw_quote": "quote"}],
            }]}],
        }
        findings = run_guardrails(synthesis)
        dangling = [f for f in findings if f.code == "dangling_topic_ref"]
        assert dangling, "Expected dangling_topic_ref finding"
        assert dangling[0].severity == "warn"

    def test_ungrounded_story_remains_error(self):
        from guardrails import run_guardrails
        synthesis = {
            "topics": [{"id": "T-01", "theme": "test"}],
            "epics": [{"id": "EP-01", "title": "Epic", "stories": [{
                "id": "ST-01", "title": "Story",
                "source_topic_id": None,  # genuinely missing
                "acceptance_criteria": ["Given x, when y, then z.", "Given a, when b, then c."],
                "priority": "Medium", "priority_rationale": "Important.",
                "tags": ["pos"], "evidence": [],
            }]}],
        }
        findings = run_guardrails(synthesis)
        ungrounded = [f for f in findings if f.code == "ungrounded_story"]
        assert ungrounded
        assert ungrounded[0].severity == "error"
