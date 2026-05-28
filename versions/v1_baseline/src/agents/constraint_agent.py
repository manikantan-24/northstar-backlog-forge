"""Constraint Extractor Agent — pulls architectural rules from wiki content.

A constraint is something the engineering team must respect when writing
stories: a platform limit, a required integration, a banned approach, a
performance budget, or a regulatory rule.

Writes to memory:
  - `constraints` — list of {id, severity, statement, source_excerpt} dicts

Severity is one of: `must`, `should`, `forbidden`.

Tools used: `claude_tool` (and `confluence_tool` if the text is empty and
we want to fetch a page by id — not exercised in the current pipeline).
"""

from __future__ import annotations

from agents.base import Agent, AgentError
from memory.audit_log import AuditLog
from memory.store import MemoryStore
from tools.base import ToolError
from tools.claude_tool import ClaudeTool
from tools.confluence_tool import ConfluenceTool


class ConstraintAgent(Agent):
    name = "constraint_extractor"

    def __init__(
        self,
        claude: ClaudeTool,
        confluence: ConfluenceTool,
        memory: MemoryStore,
        audit: AuditLog,
    ) -> None:
        super().__init__(memory=memory, audit=audit)
        self.claude = claude
        self.confluence = confluence
        self._prompt_template = self.load_prompt("constraint_extractor_prompt.md")

    def run(self, wiki_text: str) -> None:
        self.emit("started", payload={"input_chars": len(wiki_text)})

        prompt = self._prompt_template.replace("{{WIKI_CONTENT}}", wiki_text)
        try:
            parsed, usage = self.claude.call_for_json(prompt, max_tokens=4000)
        except ToolError as e:
            raise AgentError(f"Constraint Extractor LLM call failed: {e}") from e

        constraints = parsed.get("constraints", [])
        for i, c in enumerate(constraints):
            c["id"] = f"C-{i + 1:02d}"

        self.memory.put("constraints", constraints)

        self.audit.record_tool_call(
            agent=self.name,
            tool="claude",
            request={"prompt_chars": len(prompt), "max_tokens": 4000},
            response_excerpt=str(parsed)[:300],
            tokens_used=(usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0),
        )
        self.emit(
            "completed",
            payload={"constraint_count": len(constraints)},
            reasoning=f"Extracted {len(constraints)} architecture constraints from the wiki.",
        )
