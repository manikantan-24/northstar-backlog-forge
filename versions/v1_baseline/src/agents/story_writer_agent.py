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
from tools.base import ToolError
from tools.claude_tool import ClaudeTool


class StoryWriterAgent(Agent):
    name = "story_writer"

    def __init__(self, claude: ClaudeTool, memory: MemoryStore, audit: AuditLog) -> None:
        super().__init__(memory=memory, audit=audit)
        self.claude = claude
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
        for i, s in enumerate(stories):
            s.setdefault("id", f"ST-{i + 1:02d}")

        self.memory.put("stories", stories)

        self.audit.record_tool_call(
            agent=self.name,
            tool="claude",
            request={"prompt_chars": len(prompt), "max_tokens": 8000},
            response_excerpt=str(parsed)[:300],
            tokens_used=(usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0),
        )
        self.emit(
            "completed",
            payload={"story_count": len(stories)},
            reasoning=f"Drafted {len(stories)} stories across {len(topics)} topics.",
        )
