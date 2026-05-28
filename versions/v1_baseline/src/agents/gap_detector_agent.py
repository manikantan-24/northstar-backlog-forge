"""Gap Detector Agent — finds duplicates, conflicts, and gaps.

Reads from memory:
  - `stories` (from Story Writer)
  - `constraints` (from Constraint Extractor)
  - `existing_tickets` (loaded by orchestrator from --backlog)

Writes to memory:
  - `duplicates` — list of {story_id, existing_id, confidence, reason}
  - `conflicts` — list of {story_id, with, severity, reason}
  - `gaps` — list of {title, description, evidence}

Tools used:
  - `claude_tool` — final judgment
  - `jira_tool` — search existing JIRA tickets (called via memory.search_similar)
  - `github_tool` — search existing GitHub issues
  - memory vector index (built from existing_tickets) for semantic search

The agent first uses semantic similarity to narrow each new story to the
top-K most-similar existing tickets, then asks the LLM to make the final
duplicate / conflict / gap decision.
"""

from __future__ import annotations

import json

from agents.base import Agent, AgentError
from memory.audit_log import AuditLog
from memory.store import MemoryStore
from tools.base import ToolError
from tools.claude_tool import ClaudeTool
from tools.github_tool import GithubTool
from tools.jira_tool import JiraTool


TOP_K = 5


class GapDetectorAgent(Agent):
    name = "gap_detector"

    def __init__(
        self,
        claude: ClaudeTool,
        jira: JiraTool,
        github: GithubTool,
        memory: MemoryStore,
        audit: AuditLog,
    ) -> None:
        super().__init__(memory=memory, audit=audit)
        self.claude = claude
        self.jira = jira
        self.github = github
        self._prompt_template = self.load_prompt("gap_detector_prompt.md")

    def run(self) -> None:
        stories = self.memory.get("stories", [])
        constraints = self.memory.get("constraints", [])
        existing_tickets = self.memory.get("existing_tickets", [])

        if not stories:
            self.emit("skipped", reasoning="No stories to compare.")
            return

        self.emit(
            "started",
            payload={
                "story_count": len(stories),
                "existing_ticket_count": len(existing_tickets),
                "constraint_count": len(constraints),
            },
        )

        # Build the vector index over existing tickets (or skip if small)
        used_embeddings = self.memory.index_tickets(existing_tickets)
        self.emit(
            "indexed_tickets",
            payload={"used_embeddings": used_embeddings, "ticket_count": len(existing_tickets)},
            reasoning=(
                "Built semantic index for top-K candidate retrieval."
                if used_embeddings
                else "Too few tickets for embeddings; sending full list to LLM."
            ),
        )

        # Slim payloads and run semantic narrow-down per story
        slim_stories = [
            {"id": s["id"], "title": s.get("title", ""), "description": s.get("description", "")}
            for s in stories
        ]

        candidates_per_story = {}
        for s in slim_stories:
            query = f"{s['title']}. {s['description']}"
            candidates = self.memory.search_similar(query, top_k=TOP_K)
            candidates_per_story[s["id"]] = [
                {
                    "id": c.get("id") or c.get("key") or f"#{c.get('number', '?')}",
                    "title": c.get("title") or c.get("summary") or "",
                    "description": c.get("description") or c.get("body") or "",
                }
                for c in candidates
            ]

        prompt = (
            self._prompt_template
            .replace("{{NEW_STORIES_JSON}}", json.dumps(slim_stories, indent=2))
            .replace("{{CANDIDATES_JSON}}", json.dumps(candidates_per_story, indent=2))
            .replace("{{CONSTRAINTS_JSON}}", json.dumps(constraints, indent=2))
        )

        try:
            parsed, usage = self.claude.call_for_json(prompt, max_tokens=4000)
        except ToolError as e:
            raise AgentError(f"Gap Detector LLM call failed: {e}") from e

        duplicates = parsed.get("duplicates", [])
        conflicts = parsed.get("conflicts", [])
        gaps = parsed.get("gaps", [])

        self.memory.put("duplicates", duplicates)
        self.memory.put("conflicts", conflicts)
        self.memory.put("gaps", gaps)

        self.audit.record_tool_call(
            agent=self.name,
            tool="claude",
            request={"prompt_chars": len(prompt), "max_tokens": 4000},
            response_excerpt=str(parsed)[:300],
            tokens_used=(usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0),
        )
        self.emit(
            "completed",
            payload={
                "duplicate_count": len(duplicates),
                "conflict_count": len(conflicts),
                "gap_count": len(gaps),
            },
            reasoning=(
                f"Found {len(duplicates)} possible duplicates, {len(conflicts)} "
                f"constraint conflicts, and {len(gaps)} gaps in coverage."
            ),
        )
