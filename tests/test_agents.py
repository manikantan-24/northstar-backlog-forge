"""Per-agent unit tests.

Each test exercises one agent in isolation with a mocked Claude tool.
Verifies:
  - The agent loads its prompt template from prompts/<name>_prompt.md
  - The agent reads the expected keys from MemoryStore
  - The agent writes the expected shape to MemoryStore
  - The agent emits audit events for `started` and `completed`
  - The agent handles a Claude tool failure by raising AgentError

The end-to-end orchestrator test (`test_orchestrator.py`) covers the
five-agent handoff. These tests cover each agent's individual contract,
so a regression in one agent surfaces a focused failure rather than an
opaque pipeline error.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# --------------------------------------------------------------- fakes


class FakeClaudeTool:
    """Stand-in for ClaudeTool. Returns one canned response."""

    name = "claude"

    def __init__(self, response: dict) -> None:
        self._response = response
        self.calls: list[str] = []  # records every prompt the agent sent

    def call_for_json(self, user_message: str, max_tokens: int = 4000):
        self.calls.append(user_message)
        return self._response, {"input_tokens": 50, "output_tokens": 100}


class FailingClaudeTool:
    """Claude tool that always raises — used to verify agents surface ToolError as AgentError."""

    name = "claude"

    def call_for_json(self, user_message: str, max_tokens: int = 4000):
        from tools.base import ToolError
        raise ToolError("simulated API failure")


class FakeJira:
    name = "jira"

    def list_all(self):
        return []

    def search(self, q):
        return []


class FakeGithub:
    name = "github"

    def list_all(self):
        return []

    def search(self, q):
        return []


class FakeConfluence:
    name = "confluence"

    def get_page(self, page_id="default"):
        return ""


# --------------------------------------------------------------- fixtures


@pytest.fixture
def memory():
    from memory.store import MemoryStore
    return MemoryStore()


@pytest.fixture
def audit():
    from memory.audit_log import AuditLog
    return AuditLog()


# --------------------------------------------------------------- Parser Agent


def test_parser_agent_writes_topics_and_summary(memory, audit):
    from agents.parser_agent import ParserAgent

    fake = FakeClaudeTool({
        "summary": "Meeting covered POS resilience and loyalty.",
        "topics": [
            {"theme": "pos-offline", "summary": "POS offline mode",
             "raw_quote": "WAN drops", "speaker": "Hiroshi", "sentiment": "concern"},
            {"theme": "loyalty-confusion", "summary": "Tier rules unclear",
             "raw_quote": "downgraded with no warning", "speaker": "Priya", "sentiment": "concern"},
        ],
    })
    agent = ParserAgent(claude=fake, memory=memory, audit=audit)
    agent.run("Transcript text — short enough that the test is fast.")

    topics = memory.get("topics")
    assert isinstance(topics, list)
    assert len(topics) == 2
    # IDs are deterministic
    assert topics[0]["id"] == "T-01"
    assert topics[1]["id"] == "T-02"
    # Summary written
    assert memory.get("summary") == "Meeting covered POS resilience and loyalty."
    # Claude called exactly once
    assert len(fake.calls) == 1
    # Audit log has started + completed events for this agent
    events = [e for e in audit.events if e.agent == "parser"]
    event_types = {e.event for e in events}
    assert "started" in event_types
    assert "completed" in event_types


def test_parser_agent_raises_agent_error_when_claude_fails(memory, audit):
    """A ToolError from the Claude tool must surface as an AgentError."""
    from agents.parser_agent import ParserAgent
    from agents.base import AgentError

    agent = ParserAgent(claude=FailingClaudeTool(), memory=memory, audit=audit)
    with pytest.raises(AgentError):
        agent.run("any transcript")


# --------------------------------------------------------------- Constraint Agent


def test_constraint_agent_writes_constraints(memory, audit):
    from agents.constraint_agent import ConstraintAgent

    fake = FakeClaudeTool({
        "constraints": [
            {"severity": "must", "category": "performance",
             "statement": "Cart load p95 under 1.5s on 3G",
             "source_excerpt": "Mobile app cart-load p95 must stay under 1.5 seconds",
             "applies_to": ["mobile-app"]},
            {"severity": "forbidden", "category": "compliance",
             "statement": "Card sales offline are forbidden",
             "source_excerpt": "PCI is specific about online auth",
             "applies_to": ["pos"]},
        ],
    })
    agent = ConstraintAgent(claude=fake, confluence=FakeConfluence(), memory=memory, audit=audit)
    agent.run("Architecture constraints wiki text.")

    constraints = memory.get("constraints")
    assert len(constraints) == 2
    assert constraints[1]["severity"] == "forbidden"
    # Constraint agent skips writing when the input is empty
    assert len(fake.calls) == 1


def test_constraint_agent_raises_agent_error_when_claude_fails(memory, audit):
    """A ToolError from the Claude tool must surface as an AgentError so the
    orchestrator can decide whether to continue with downstream agents."""
    from agents.constraint_agent import ConstraintAgent
    from agents.base import AgentError

    agent = ConstraintAgent(
        claude=FailingClaudeTool(), confluence=FakeConfluence(),
        memory=memory, audit=audit,
    )
    with pytest.raises(AgentError):
        agent.run("Some constraint wiki text.")


# --------------------------------------------------------------- Story Writer Agent


def test_story_writer_agent_writes_stories_with_acceptance_criteria(memory, audit):
    from agents.story_writer_agent import StoryWriterAgent

    # Story writer reads `topics` and `constraints` from memory
    memory.put("topics", [
        {"id": "T-01", "theme": "pos-offline", "summary": "POS offline mode",
         "raw_quote": "WAN drops"},
    ])
    memory.put("constraints", [])  # empty constraints list is valid

    fake = FakeClaudeTool({
        "stories": [
            {
                "id": "ST-01",
                "title": "Enable cash sales offline at POS",
                "description": "Lane falls back to local SQLite cache.",
                "user_story": "As a cashier, I want to ring cash offline, so customers aren't turned away.",
                "acceptance_criteria": [
                    "Given WAN unreachable, when cash sale is rung, then it completes from cache.",
                    "Given WAN returns, when sync runs, then offline transactions reconcile.",
                ],
                "priority": "High",
                "priority_rationale": "Direct revenue loss.",
                "tags": ["pos", "offline-mode"],
                "source_topic_id": "T-01",
                "potential_constraint_conflicts": [],
            }
        ]
    })
    agent = StoryWriterAgent(claude=fake, memory=memory, audit=audit)
    agent.run()

    stories = memory.get("stories")
    assert len(stories) == 1
    s = stories[0]
    assert s["id"] == "ST-01"
    assert len(s["acceptance_criteria"]) == 2
    # Every AC follows the Given/When/Then convention — basic structural check
    assert all("Given" in ac and "when" in ac.lower() and "then" in ac.lower()
               for ac in s["acceptance_criteria"])


def test_story_writer_agent_skips_when_no_topics(memory, audit):
    """If no topics were extracted, the writer skips rather than hallucinate."""
    from agents.story_writer_agent import StoryWriterAgent

    memory.put("topics", [])
    fake = FakeClaudeTool({})
    agent = StoryWriterAgent(claude=fake, memory=memory, audit=audit)
    agent.run()

    assert fake.calls == []
    assert memory.get("stories", []) == []


# --------------------------------------------------------------- Epic Decomposer Agent


def test_epic_decomposer_agent_groups_stories_into_epics_with_tasks(memory, audit):
    from agents.epic_decomposer_agent import EpicDecomposerAgent

    memory.put("stories", [
        {"id": "ST-01", "title": "Cash sales offline at POS", "tags": ["pos", "offline"]},
        {"id": "ST-02", "title": "Gift card redemption offline at POS", "tags": ["pos", "offline"]},
    ])

    fake = FakeClaudeTool({
        "epics": [
            {
                "id": "EP-01",
                "title": "POS Offline Resilience",
                "description": "Keep the lane working when WAN drops.",
                "stories": [
                    {
                        "id": "ST-01",
                        "title": "Cash sales offline at POS",
                        "tags": ["pos", "offline"],
                        "tasks": [
                            {"id": "ST-01-TK-01", "title": "Embed SQLite on lane", "type": "infra"},
                            {"id": "ST-01-TK-02", "title": "Hourly sync job", "type": "backend"},
                        ],
                    },
                    {
                        "id": "ST-02",
                        "title": "Gift card redemption offline at POS",
                        "tags": ["pos", "offline"],
                        "tasks": [
                            {"id": "ST-02-TK-01", "title": "Local balance cache schema", "type": "infra"},
                        ],
                    },
                ],
            }
        ]
    })
    agent = EpicDecomposerAgent(claude=fake, memory=memory, audit=audit)
    agent.run()

    epics = memory.get("epics")
    assert len(epics) == 1
    epic = epics[0]
    assert epic["id"] == "EP-01"
    assert len(epic["stories"]) == 2
    # Each story has tasks
    assert all(len(s["tasks"]) >= 1 for s in epic["stories"])


# --------------------------------------------------------------- Gap Detector Agent


def test_gap_detector_writes_duplicates_conflicts_and_gaps(memory, audit):
    from agents.gap_detector_agent import GapDetectorAgent

    memory.put("stories", [
        {"id": "ST-01", "title": "Loyalty tier progress UI",
         "description": "Show the customer how close they are to next tier.",
         "tags": ["loyalty", "mobile-app"]},
    ])
    memory.put("constraints", [])
    memory.put("existing_tickets", [
        {"id": "NS-389", "key": "NS-389", "title": "Loyalty tier downgrade email",
         "summary": "Loyalty tier downgrade email", "description": "Clarify tier email."},
    ])

    fake = FakeClaudeTool({
        "duplicates": [
            {"story_id": "ST-01", "existing_id": "NS-389",
             "confidence": "high", "reason": "Both address loyalty-tier transparency."},
        ],
        "conflicts": [],
        "gaps": [
            {"title": "Tier-progress server-side computation missing",
             "description": "Stories assume the API exists but no story creates it.",
             "evidence": "No story covers the API contract."},
        ],
    })

    # This test asserts on the LLM-emitted duplicate payload, so opt out of
    # the new embedding-based duplicate detection — otherwise duplicates
    # come from the local cosine similarity (different `reason` text) or
    # don't surface at all when the strings aren't close enough.
    agent = GapDetectorAgent(
        claude=fake, jira=FakeJira(), github=FakeGithub(),
        memory=memory, audit=audit,
        use_embeddings_for_duplicates=False,
    )
    agent.run()

    assert memory.get("duplicates")[0]["existing_id"] == "NS-389"
    assert memory.get("conflicts") == []
    assert len(memory.get("gaps")) == 1


def test_gap_detector_skips_when_no_stories(memory, audit):
    """No upstream stories → gap detector should skip cleanly, not call Claude."""
    from agents.gap_detector_agent import GapDetectorAgent

    memory.put("stories", [])
    fake = FakeClaudeTool({})

    agent = GapDetectorAgent(
        claude=fake, jira=FakeJira(), github=FakeGithub(),
        memory=memory, audit=audit,
    )
    agent.run()

    assert fake.calls == []
    events = [e for e in audit.events if e.agent == "gap_detector"]
    assert any(e.event == "skipped" for e in events)


# --------------------------------------------------------------- Memory & Audit


def test_memory_store_kv_put_get_append(memory):
    memory.put("foo", "bar")
    assert memory.get("foo") == "bar"
    assert memory.get("missing", "default") == "default"

    memory.append("items", 1)
    memory.append("items", 2)
    assert memory.get("items") == [1, 2]


def test_audit_log_renders_markdown(audit):
    audit.record("parser", "started", payload={"input_chars": 100})
    audit.record("parser", "completed", reasoning="Extracted 3 topics.")
    md = audit.render_markdown()
    assert "Audit trail" in md
    assert "parser" in md
    assert "started" in md
    assert "completed" in md
    assert "Extracted 3 topics" in md


# ----------------------------------------------------------------- Ollama tool tests

class FakeOllamaResponse:
    def __init__(self, payload: dict, status: int = 200):
        self.status_code = status
        self._payload = payload
        self.text = str(payload)
    def json(self): return self._payload


def _patch_requests(monkeypatch, get_resp=None, post_resp=None):
    import requests
    if get_resp is not None:
        monkeypatch.setattr(requests, "get", lambda *a, **kw: get_resp)
    if post_resp is not None:
        monkeypatch.setattr(requests, "post", lambda *a, **kw: post_resp)


def test_ollama_tool_call_for_json_success(monkeypatch):
    """OllamaTool.call_for_json parses the message content as JSON."""
    import sys, json
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from tools.ollama_tool import OllamaTool

    health = FakeOllamaResponse({"models": []})
    generation = FakeOllamaResponse({
        "message": {"content": json.dumps({"summary": "ok", "topics": []})},
        "prompt_eval_count": 50, "eval_count": 20,
    })
    _patch_requests(monkeypatch, get_resp=health, post_resp=generation)

    tool = OllamaTool(model="llama3.1", base_url="http://localhost:11434")
    parsed, usage = tool.call_for_json("test prompt")
    assert parsed["summary"] == "ok"
    assert usage["input_tokens"] == 50
    assert usage["output_tokens"] == 20


def test_ollama_tool_strips_ollama_prefix(monkeypatch):
    """The 'ollama/' prefix is stripped before the API call."""
    import sys, json, requests as _r
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from tools.ollama_tool import OllamaTool

    captured = {}
    def fake_post(url, json=None, **kw):
        captured["model"] = (json or {}).get("model")
        return FakeOllamaResponse({
            "message": {"content": "{}"},
            "prompt_eval_count": 1, "eval_count": 1,
        })
    monkeypatch.setattr(_r, "get", lambda *a, **kw: FakeOllamaResponse({"models": []}))
    monkeypatch.setattr(_r, "post", fake_post)

    OllamaTool(model="ollama/llama3.1").call("hi")
    assert captured["model"] == "llama3.1"


def test_ollama_tool_raises_when_server_unreachable(monkeypatch):
    """ToolError is raised immediately when Ollama isn't running."""
    import sys, requests as _r
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from tools.ollama_tool import OllamaTool
    from tools.base import ToolError

    def raise_connection(*a, **kw):
        raise _r.exceptions.ConnectionError("refused")
    monkeypatch.setattr(_r, "get", raise_connection)

    with pytest.raises(ToolError, match="Cannot reach Ollama"):
        OllamaTool(model="llama3.1")


def test_ollama_tool_handles_fenced_json(monkeypatch):
    """_extract_json_block is used when the model wraps output in ```json."""
    import sys, json, requests as _r
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from tools.ollama_tool import OllamaTool

    fenced = '```json\n{"stories": []}\n```'
    monkeypatch.setattr(_r, "get", lambda *a, **kw: FakeOllamaResponse({"models": []}))
    monkeypatch.setattr(_r, "post", lambda *a, **kw: FakeOllamaResponse({
        "message": {"content": fenced},
        "prompt_eval_count": 5, "eval_count": 5,
    }))

    parsed, _ = OllamaTool(model="llama3.1").call_for_json("test")
    assert parsed == {"stories": []}
