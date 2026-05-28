"""Story Writer Agent — drafts user stories from topics, aware of constraints.

Reads from memory:
  - `topics` (from Parser)
  - `constraints` (from Constraint Extractor) — used as context so the model
    avoids drafting stories that violate them

Writes to memory:
  - `stories` — list of full story dicts:
    {
        id, title, description, user_story, acceptance_criteria, priority,
        priority_rationale, tags, source_topic_id, conflicts_with_constraints
    }

Tools used: `claude_tool` only.
"""

from __future__ import annotations

import json

from agents.base import Agent, AgentError
from memory.audit_log import AuditLog
from memory.store import MemoryStore
from tools.base import Tool, ToolError


class StoryWriterAgent(Agent):
    name = "story_writer"

    def __init__(
        self,
        claude: Tool | None = None,
        memory: MemoryStore | None = None,
        audit: AuditLog | None = None,
        *,
        tool: Tool | None = None,
    ) -> None:
        super().__init__(memory=memory, audit=audit)
        self.claude = tool or claude
        if self.claude is None:
            raise AgentError("StoryWriterAgent requires an LLM tool (claude= or tool=).")
        self._prompt_template = self.load_prompt("story_writer_prompt.md")

    def run(self) -> None:
        topics = self.memory.get("topics", [])
        constraints = self.memory.get("constraints", [])

        if not topics:
            self.emit("skipped", reasoning="No topics in memory; nothing to write stories for.")
            return

        self.emit("started", payload={"topic_count": len(topics), "constraint_count": len(constraints)})

        prompt = (
            self._prompt_template
            .replace("{{TOPICS_JSON}}", json.dumps(topics, indent=2))
            .replace("{{CONSTRAINTS_JSON}}", json.dumps(constraints, indent=2))
        )
        try:
            parsed, usage = self.claude.call_for_json(prompt, max_tokens=8000)
        except ToolError as e:
            raise AgentError(f"Story Writer LLM call failed: {e}") from e

        stories = parsed.get("stories", [])
        # Build a lookup from topic id → topic so we can attach the source
        # quote as evidence on every story. Reviewers (and downstream Jira
        # publishing) get a click-through from the story to the customer
        # words that motivated it, without re-prompting the LLM.
        topics_by_id = {t.get("id"): t for t in topics if isinstance(t, dict)}
        for i, s in enumerate(stories):
            s.setdefault("id", f"ST-{i + 1:02d}")
            self._attach_evidence(s, topics_by_id)

        self.memory.put("stories", stories)

        import json as _json
        self.audit.record_tool_call(
            agent=self.name,
            tool=getattr(self.claude, "name", "claude"),
            request={"prompt_chars": len(prompt), "max_tokens": 8000},
            response_excerpt=str(parsed)[:300],
            tokens_used=(usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0),
            usage=usage,
            prompt=prompt,
            response_text=_json.dumps(parsed, indent=2),
        )
        self.emit(
            "completed",
            payload={"story_count": len(stories)},
            reasoning=f"Drafted {len(stories)} stories across {len(topics)} topics.",
        )

    @staticmethod
    def _attach_evidence(story: dict, topics_by_id: dict) -> None:
        """Add an `evidence` block to a story citing the source topic.

        Evidence is the parser-extracted raw_quote plus speaker / sentiment
        metadata, plus the topic id. If the story has no source_topic_id
        (or it doesn't match any parsed topic) evidence is set to an empty
        list — never None — so downstream code can iterate safely.
        """
        sid = story.get("source_topic_id")
        topic = topics_by_id.get(sid) if sid else None
        if not topic:
            story.setdefault("evidence", [])
            return
        story["evidence"] = [{
            "topic_id": sid,
            "theme": topic.get("theme", ""),
            "raw_quote": topic.get("raw_quote", ""),
            "speaker": topic.get("speaker", ""),
            "sentiment": topic.get("sentiment", ""),
        }]
