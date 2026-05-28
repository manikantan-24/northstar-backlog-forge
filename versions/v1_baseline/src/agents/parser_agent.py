"""Parser Agent — extracts distinct topics from raw transcript text.

A topic is a coherent ask, complaint, or observation from the source. The
Story Writer downstream uses topics as anchors so each story traces back
to a specific source quote.

Writes to memory:
  - `topics` — list of {id, raw_quote, theme, summary} dicts
  - `summary` — short overall summary of the transcript

Tools used: `claude_tool` only.
"""

from __future__ import annotations

from agents.base import Agent, AgentError
from memory.audit_log import AuditLog
from memory.store import MemoryStore
from tools.base import ToolError
from tools.claude_tool import ClaudeTool


class ParserAgent(Agent):
    name = "parser"

    def __init__(self, claude: ClaudeTool, memory: MemoryStore, audit: AuditLog) -> None:
        super().__init__(memory=memory, audit=audit)
        self.claude = claude
        self._prompt_template = self.load_prompt("parser_prompt.md")

    def run(self, transcript_text: str) -> None:
        self.emit("started", payload={"input_chars": len(transcript_text)})

        prompt = self._prompt_template.replace("{{TRANSCRIPT}}", transcript_text)
        try:
            parsed, usage = self.claude.call_for_json(prompt, max_tokens=4000)
        except ToolError as e:
            raise AgentError(f"Parser LLM call failed: {e}") from e

        topics = parsed.get("topics", [])
        summary = parsed.get("summary", "")

        # Assign deterministic IDs
        for i, t in enumerate(topics):
            t["id"] = f"T-{i + 1:02d}"

        self.memory.put("topics", topics)
        if summary:
            self.memory.put("summary", summary)

        self.audit.record_tool_call(
            agent=self.name,
            tool="claude",
            request={"prompt_chars": len(prompt), "max_tokens": 4000},
            response_excerpt=str(parsed)[:300],
            tokens_used=(usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0),
        )
        self.emit(
            "completed",
            payload={"topic_count": len(topics)},
            reasoning=f"Extracted {len(topics)} distinct topics from the transcript.",
        )
