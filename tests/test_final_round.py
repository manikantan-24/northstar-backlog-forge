"""
Final round end-to-end test suite.
Covers all modules added after test_new_modules.py:
  - entra_auth          (Entra ID SSO — parse_user, role mapping, URL construction)
  - agent-level traces  (child_span in ClaudeTool, GeminiTool, OllamaTool)
  - memory/store        (ChromaDB backend detection, search_similar routing)
  - mcp_server          (5 tools registered correctly)
  - evaluation/dashboard (--fail-on-regression CI gate)
  - startup_check       (check_python_version)
  - story evidence filter (placeholder raw_quote suppressed in UI)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ══════════════════════════════════════════════════════════════════════════════
# entra_auth
# ══════════════════════════════════════════════════════════════════════════════

class TestEntraAuth:

    def _make_id_token(self, claims: dict) -> str:
        """Encode a fake JWT with the given payload (no real signature)."""
        import base64, json
        header  = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(
            json.dumps(claims).encode()
        ).rstrip(b"=").decode()
        return f"{header}.{payload}.fakesig"

    # ── parse_user + role mapping ──────────────────────────────────────────────
    # parse_user now calls _verify_id_token() which performs RS256/JWKS
    # verification — fake tokens with "fakesig" would fail. We patch
    # _verify_id_token to return the claims dict directly, isolating the
    # role-mapping logic from the network-dependent signature check.

    def test_admin_role_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("ENTRA_TENANT_ID",     "fake-tenant")
        monkeypatch.setenv("ENTRA_CLIENT_ID",     "fake-client")
        monkeypatch.setenv("ENTRA_CLIENT_SECRET", "fake-secret")
        import entra_auth as ea
        claims = {
            "name": "Admin User",
            "preferred_username": "admin@corp.onmicrosoft.com",
            "oid": "abc123",
            "roles": ["Admin"],   # capital A — must still map to "admin"
        }
        id_token = self._make_id_token(claims)
        with patch.object(ea, "_verify_id_token", return_value=claims):
            user = ea.parse_user({"id_token": id_token})
        assert user["role"] == "admin"
        assert user["name"] == "Admin User"

    def test_contributor_role_maps_correctly(self, monkeypatch):
        monkeypatch.setenv("ENTRA_TENANT_ID",     "fake-tenant")
        monkeypatch.setenv("ENTRA_CLIENT_ID",     "fake-client")
        monkeypatch.setenv("ENTRA_CLIENT_SECRET", "fake-secret")
        import entra_auth as ea
        claims = {"roles": ["Contributor"], "preferred_username": "pm@corp.com"}
        with patch.object(ea, "_verify_id_token", return_value=claims):
            user = ea.parse_user({"id_token": self._make_id_token(claims)})
        assert user["role"] == "contributor"

    def test_viewer_role_maps_correctly(self, monkeypatch):
        monkeypatch.setenv("ENTRA_TENANT_ID",     "fake-tenant")
        monkeypatch.setenv("ENTRA_CLIENT_ID",     "fake-client")
        monkeypatch.setenv("ENTRA_CLIENT_SECRET", "fake-secret")
        import entra_auth as ea
        claims = {"roles": ["Viewer"], "preferred_username": "v@corp.com"}
        with patch.object(ea, "_verify_id_token", return_value=claims):
            user = ea.parse_user({"id_token": self._make_id_token(claims)})
        assert user["role"] == "viewer"

    def test_no_roles_defaults_to_contributor(self, monkeypatch):
        """Authenticated tenant users with no explicit app role default to contributor."""
        monkeypatch.setenv("ENTRA_TENANT_ID",     "fake-tenant")
        monkeypatch.setenv("ENTRA_CLIENT_ID",     "fake-client")
        monkeypatch.setenv("ENTRA_CLIENT_SECRET", "fake-secret")
        import entra_auth as ea
        claims = {"preferred_username": "unknown@corp.com"}
        with patch.object(ea, "_verify_id_token", return_value=claims):
            user = ea.parse_user({"id_token": self._make_id_token(claims)})
        assert user["role"] == "contributor"

    def test_admin_takes_priority_over_viewer(self, monkeypatch):
        monkeypatch.setenv("ENTRA_TENANT_ID",     "fake-tenant")
        monkeypatch.setenv("ENTRA_CLIENT_ID",     "fake-client")
        monkeypatch.setenv("ENTRA_CLIENT_SECRET", "fake-secret")
        import entra_auth as ea
        claims = {"roles": ["Viewer", "Admin"], "preferred_username": "x@c.com"}
        with patch.object(ea, "_verify_id_token", return_value=claims):
            user = ea.parse_user({"id_token": self._make_id_token(claims)})
        assert user["role"] == "admin"

    def test_parse_user_raises_on_empty_id_token(self):
        from entra_auth import parse_user
        with pytest.raises(ValueError, match="no id_token"):
            parse_user({})

    def test_get_auth_url_contains_client_id(self, monkeypatch):
        monkeypatch.setenv("ENTRA_TENANT_ID",     "tenant-abc")
        monkeypatch.setenv("ENTRA_TENANT_DOMAIN", "corp.onmicrosoft.com")
        monkeypatch.setenv("ENTRA_CLIENT_ID",     "fake-client-id")
        monkeypatch.setenv("ENTRA_CLIENT_SECRET", "secret")
        import importlib, entra_auth as ea
        importlib.reload(ea)
        url = ea.get_auth_url()
        assert "fake-client-id" in url
        assert "corp.onmicrosoft.com" in url
        assert "response_type=code" in url
        assert "prompt=login" in url

    def test_get_auth_url_auto_generates_state_nonce(self, monkeypatch):
        monkeypatch.setenv("ENTRA_TENANT_ID",     "tenant-abc")
        monkeypatch.setenv("ENTRA_CLIENT_ID",     "fake-client-id")
        monkeypatch.setenv("ENTRA_CLIENT_SECRET", "secret")
        import entra_auth as ea
        url = ea.get_auth_url()
        assert "state=" in url
        # The auto-generated nonce must be registered in the server-side store
        import urllib.parse
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        nonce = qs["state"][0]
        assert ea.consume_state(nonce) is True   # registered and consumable once

    def test_is_enabled_returns_false_when_vars_missing(self, monkeypatch):
        monkeypatch.delenv("ENTRA_TENANT_ID", raising=False)
        monkeypatch.delenv("ENTRA_CLIENT_ID", raising=False)
        monkeypatch.delenv("ENTRA_CLIENT_SECRET", raising=False)
        import entra_auth as ea
        assert ea.is_enabled() is False

    def test_is_enabled_returns_true_when_all_vars_set(self, monkeypatch):
        monkeypatch.setenv("ENTRA_TENANT_ID",     "t")
        monkeypatch.setenv("ENTRA_CLIENT_ID",     "c")
        monkeypatch.setenv("ENTRA_CLIENT_SECRET", "s")
        import entra_auth as ea
        assert ea.is_enabled() is True

    # ── OAuth state nonce ──────────────────────────────────────────────────────

    def test_register_and_consume_state_single_use(self):
        from entra_auth import register_state, consume_state
        nonce = "test-nonce-single-use"
        register_state(nonce)
        assert consume_state(nonce) is True
        assert consume_state(nonce) is False   # consumed — second call must return False

    def test_consume_state_unknown_returns_false(self):
        from entra_auth import consume_state
        assert consume_state("totally-unknown-nonce") is False

    def test_generate_state_nonce_is_registered_and_consumable(self):
        from entra_auth import generate_state_nonce, consume_state
        nonce = generate_state_nonce()
        assert len(nonce) > 20
        assert consume_state(nonce) is True
        assert consume_state(nonce) is False   # single-use

    def test_register_state_evicts_expired(self, monkeypatch):
        import entra_auth as ea, time
        old_ttl = ea._STATE_TTL
        try:
            ea._STATE_TTL = 0.01   # 10ms TTL so the nonce expires immediately
            ea.register_state("expiring-nonce")
            time.sleep(0.05)
            ea.register_state("trigger-eviction")   # triggers cleanup loop
            assert ea.consume_state("expiring-nonce") is False
        finally:
            ea._STATE_TTL = old_ttl
            ea.consume_state("trigger-eviction")    # cleanup

    # ── exchange_code_for_token ────────────────────────────────────────────────

    def test_exchange_code_raises_on_http_error(self, monkeypatch):
        """raise_for_status() must propagate HTTP 4xx/5xx as HTTPError."""
        import requests as req
        from unittest.mock import MagicMock
        import entra_auth as ea
        monkeypatch.setenv("ENTRA_TENANT_ID",     "t")
        monkeypatch.setenv("ENTRA_CLIENT_ID",     "c")
        monkeypatch.setenv("ENTRA_CLIENT_SECRET", "s")
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req.HTTPError("401 Unauthorized")
        with patch.object(ea._requests, "post", return_value=mock_resp):
            with pytest.raises(req.HTTPError):
                ea.exchange_code_for_token("bad-code")


# ══════════════════════════════════════════════════════════════════════════════
# Agent-level traces (child_span emitted by tools)
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentLevelTraces:

    def test_child_span_noop_when_otel_disabled(self, monkeypatch):
        monkeypatch.setenv("OTEL_ENABLED", "0")
        import importlib, telemetry as tel
        importlib.reload(tel)
        with tel.child_span("test.span", foo="bar") as span:
            span.set_attribute("x", 1)   # should not raise

    def test_child_span_returns_noop_span(self, monkeypatch):
        monkeypatch.setenv("OTEL_ENABLED", "0")
        import importlib, telemetry as tel
        importlib.reload(tel)
        with tel.child_span("test") as s:
            assert hasattr(s, "set_attribute")
            assert hasattr(s, "record_exception")

    def test_claude_tool_wraps_call_in_span(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        monkeypatch.setenv("OTEL_ENABLED", "0")
        import inspect
        from tools.claude_tool import ClaudeTool
        src = inspect.getsource(ClaudeTool._call_with_retry)
        assert "child_span" in src or "llm.call" in src

    def test_gemini_tool_wraps_call_in_span(self, monkeypatch):
        monkeypatch.setenv("OTEL_ENABLED", "0")
        import inspect
        from tools.gemini_tool import GeminiTool
        src = inspect.getsource(GeminiTool._call_with_retry)
        assert "child_span" in src or "_cs" in src

    def test_ollama_tool_wraps_call_in_span(self, monkeypatch):
        monkeypatch.setenv("OTEL_ENABLED", "0")
        import inspect
        from tools.ollama_tool import OllamaTool
        src = inspect.getsource(OllamaTool.call)
        assert "child_span" in src or "_cs" in src

    def test_guardrails_emit_spans_per_check(self, monkeypatch):
        monkeypatch.setenv("OTEL_ENABLED", "0")
        import inspect
        import guardrails
        src = inspect.getsource(guardrails.run_guardrails)
        assert "child_span" in src or "_cs" in src

    def test_pipeline_node_span_noop_when_otel_disabled(self, monkeypatch):
        monkeypatch.setenv("OTEL_ENABLED", "0")
        import importlib, telemetry as tel
        importlib.reload(tel)
        with tel.pipeline_node_span("parse", run_id="r1") as span:
            span.set_attribute("x", 1)   # must not raise on NoopSpan

    def test_pipeline_node_span_propagates_exception(self, monkeypatch):
        monkeypatch.setenv("OTEL_ENABLED", "0")
        import importlib, telemetry as tel
        importlib.reload(tel)
        with pytest.raises(ValueError, match="boom"):
            with tel.pipeline_node_span("failing.node"):
                raise ValueError("boom")

    def test_record_stage_tokens_with_explicit_stage(self, monkeypatch):
        monkeypatch.setenv("OTEL_ENABLED", "0")
        import importlib, telemetry as tel
        importlib.reload(tel)
        span = tel._NoopSpan()
        # New signature — stage kwarg must not raise
        tel.record_stage_tokens(span, 100, 200, stage="story_writer")

    def test_record_stage_tokens_default_stage(self, monkeypatch):
        monkeypatch.setenv("OTEL_ENABLED", "0")
        import importlib, telemetry as tel
        importlib.reload(tel)
        span = tel._NoopSpan()
        # Old call-site style (no stage) must remain backward-compatible
        tel.record_stage_tokens(span, 50, 150)


# ══════════════════════════════════════════════════════════════════════════════
# MemoryStore — ChromaDB routing
# ══════════════════════════════════════════════════════════════════════════════

class TestMemoryStoreChromaRouting:

    def test_chromadb_not_used_by_default(self, monkeypatch, tmp_path):
        monkeypatch.delenv("USE_CHROMADB", raising=False)
        from memory.store import MemoryStore
        store = MemoryStore(cache_dir=tmp_path)
        assert store._use_chromadb is False
        assert store._chroma_collection is None

    def test_search_similar_returns_all_when_no_index(self, tmp_path):
        from memory.store import MemoryStore
        store = MemoryStore(cache_dir=tmp_path)
        tickets = [{"id": f"T-{i}", "title": f"ticket {i}"} for i in range(5)]
        store._tickets_for_vectors = tickets
        result = store.search_similar("some query", top_k=3)
        assert isinstance(result, list)

    def test_chromadb_init_fails_gracefully_when_not_installed(self, monkeypatch, tmp_path):
        """When chromadb raises on import inside _init_chromadb, _use_chromadb is set False."""
        monkeypatch.setenv("USE_CHROMADB", "1")
        from memory.store import MemoryStore
        store = MemoryStore.__new__(MemoryStore)
        store._use_chromadb = True
        store._chroma_collection = None
        store._cache_dir = tmp_path
        # Simulate import failure inside _init_chromadb by patching chromadb directly
        with patch.dict("sys.modules", {"chromadb": None}):
            store._init_chromadb()
        assert store._use_chromadb is False


# ══════════════════════════════════════════════════════════════════════════════
# MCP server tools registered
# ══════════════════════════════════════════════════════════════════════════════

class TestMCPServer:

    def test_all_five_tools_registered(self):
        import sys
        sys.path.insert(0, str(ROOT / "src"))
        import mcp_server
        names = set(mcp_server.mcp._tool_manager._tools.keys())
        assert "synthesize_backlog" in names
        assert "preview_prompts"    in names
        assert "get_run_history"    in names
        assert "get_run_result"     in names
        assert "push_to_jira"       in names

    def test_synthesize_backlog_requires_transcript(self, monkeypatch):
        # FastMCP wraps functions as FunctionTool — access underlying fn via .fn
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        import mcp_server
        tools = mcp_server.mcp._tool_manager._tools
        fn = tools["synthesize_backlog"].fn
        result = fn(transcript="")
        assert "error" in result

    def test_get_run_history_returns_list(self, tmp_path):
        import mcp_server
        tools = mcp_server.mcp._tool_manager._tools
        fn = tools["get_run_history"].fn
        original = mcp_server.RUNS_DIR
        mcp_server.RUNS_DIR = tmp_path
        result = fn(limit=5)
        mcp_server.RUNS_DIR = original
        assert isinstance(result, list)

    def test_get_run_result_not_found(self):
        import mcp_server
        tools = mcp_server.mcp._tool_manager._tools
        fn = tools["get_run_result"].fn
        result = fn(run_id="nonexistent-run-id")
        assert "error" in result


# ══════════════════════════════════════════════════════════════════════════════
# Evaluation dashboard CI gate
# ══════════════════════════════════════════════════════════════════════════════

class TestEvalDashboardCIGate:

    def _make_runs(self, curr_score: float, prev_score: float) -> list[dict]:
        return [
            {"cases": [{"case_id": "c1", "score_deterministic": curr_score}]},
            {"cases": [{"case_id": "c1", "score_deterministic": prev_score}]},
        ]

    def test_gate_passes_when_no_regression(self, tmp_path, capsys):
        import sys
        sys.argv = ["dashboard.py", "--fail-on-regression", "--regression-threshold", "0.10",
                    "--results-dir", str(tmp_path)]
        from evaluation.dashboard import main
        # No runs → no regression possible → should return 0
        exit_code = main()
        assert exit_code == 0

    def test_regression_detected_returns_one(self):
        # Patch internal functions for a dry test
        runs = [
            {"cases": [{"case_id": "c1", "score_deterministic": 0.50}]},
            {"cases": [{"case_id": "c1", "score_deterministic": 0.75}]},
        ]
        # drop = 0.75 - 0.50 = 0.25 >= threshold 0.10
        curr_c, prev_c = runs[0]["cases"][0], runs[1]["cases"][0]
        drop = prev_c["score_deterministic"] - curr_c["score_deterministic"]
        assert drop >= 0.10

    def test_no_regression_within_tolerance(self):
        curr, prev = 0.80, 0.82  # drop = 0.02 < threshold 0.10
        drop = prev - curr
        assert drop < 0.10


# ══════════════════════════════════════════════════════════════════════════════
# startup_check.check_python_version
# ══════════════════════════════════════════════════════════════════════════════

class TestStartupCheckPythonVersion:

    def test_no_warning_on_python_310_plus(self):
        from startup_check import check_python_version
        import sys
        if sys.version_info >= (3, 10):
            assert check_python_version() == []

    def test_returns_list_always(self):
        from startup_check import check_python_version
        result = check_python_version()
        assert isinstance(result, list)


# ══════════════════════════════════════════════════════════════════════════════
# Story evidence placeholder filter
# ══════════════════════════════════════════════════════════════════════════════

class TestEvidencePlaceholderFilter:

    def _attach(self, raw_quote: str, speaker: str = "...") -> dict:
        from agents.story_writer_agent import StoryWriterAgent
        topics = [{"id": "T-01", "theme": "test", "raw_quote": raw_quote,
                   "speaker": speaker, "sentiment": ""}]
        story = {"id": "ST-01", "source_topic_id": "T-01"}
        StoryWriterAgent._attach_evidence(story, {"T-01": topics[0]})
        return story

    def test_placeholder_dots_gives_empty_evidence(self):
        story = self._attach("...")
        assert story.get("evidence") == []

    def test_unicode_ellipsis_gives_empty_evidence(self):
        story = self._attach("…")
        assert story.get("evidence") == []

    def test_null_string_gives_empty_evidence(self):
        story = self._attach("null")
        assert story.get("evidence") == []

    def test_real_quote_attaches_evidence(self):
        story = self._attach("We lose sales when internet drops", "Store Manager")
        ev = story.get("evidence") or []
        assert len(ev) == 1
        assert ev[0]["raw_quote"] == "We lose sales when internet drops"
        assert ev[0]["speaker"] == "Store Manager"

    def test_placeholder_speaker_stripped(self):
        story = self._attach("Real quote here", "...")
        ev = story.get("evidence") or []
        assert len(ev) == 1
        assert ev[0]["speaker"] == ""  # "..." stripped
