# Agent design

This document explains the multi-agent architecture: why five agents, why this ordering, and what each agent contracts to do.

## Why multi-agent (and not just bigger prompts)

A v1 single-agent system can take a transcript and produce a flat list of stories. That works when:

- Input is a single source
- Output is a flat list
- Detection is duplicate-only

This system breaks all three assumptions:

- Inputs are heterogeneous (transcripts, wikis, JIRA exports, GitHub issues)
- Output is hierarchical (epics → stories → tasks)
- Detection spans duplicates, conflicts, *and* gaps

You could in principle cram all of this into one giant prompt. We tried that route during v1 prototyping. Three failure modes appeared:

1. **The model "saves tokens."** When asked to do extraction + dedup in one shot, dedup gets shorter and lower-quality.
2. **Sequencing is implicit.** The story writer needs to know what the constraints are *before* it writes. In a single prompt this is opaque; in a multi-agent pipeline it's enforced by ordering.
3. **Auditability collapses.** A single 6000-token response is hard to audit. A sequence of small focused responses, each captured to an audit log, is easy.

## Why bounded multi-agent (and not autonomous)

There is a spectrum of "multi-agent" designs:

- **Bounded pipeline** — fixed list of specialized agents in a fixed order. (What we built.)
- **Free-form loop** — a planner agent decides which agent to call next, when to stop. Frameworks like AutoGPT, LangGraph (with cycles), and CrewAI (with hierarchies) sit here.

We chose bounded because:

- **Reproducible** — same input, same agent order, comparable output. Free-form runs are non-deterministic.
- **Testable** — each agent has a single mocked-Claude unit test. Free-form loops are hard to test because they don't have a stable trace.
- **Cost-bounded** — exactly N API calls per run, where N is known up front (~6-8). Free-form runs can spend $10 of API budget on a single run if the planner gets stuck.
- **Auditable** — the audit log is a linear trace. Free-form is a graph.

For our use case (backlog synthesis where the workflow is well-understood) the bounded pattern delivers the wins of specialization without the unpredictability.

## The five agents and their contracts

Each agent has a single responsibility. Here's the contract.

### Parser Agent
- **Input:** raw transcript text
- **Output (to memory):** `topics` — list of `{id, theme, summary, raw_quote, speaker, sentiment}`
- **Tools used:** `claude_tool`
- **Failure mode:** if the transcript has nothing actionable, returns an empty topic list with an explanatory summary. Pipeline continues with no stories produced — which is the correct behavior.

### Constraint Extractor Agent
- **Input:** wiki / Confluence content
- **Output (to memory):** `constraints` — list of `{id, severity, category, statement, source_excerpt, applies_to}`
- **Tools used:** `claude_tool`, `confluence_tool` (optional)
- **Failure mode:** if the wiki text is missing or empty, the agent is skipped and the pipeline continues without architectural constraint awareness. Downstream agents flag fewer conflicts as a result, but the rest still works.

### Story Writer Agent
- **Input:** `topics` + `constraints` from memory
- **Output (to memory):** `stories` — list of full story records with `acceptance_criteria`, `priority`, `tags`, `source_topic_id`, `potential_constraint_conflicts`
- **Tools used:** `claude_tool`
- **Failure mode:** if topics are empty, agent is skipped. If the LLM call fails permanently, the pipeline produces no stories — but topics and constraints are still preserved in memory and the audit log.

### Epic Decomposer Agent
- **Input:** `stories` from memory
- **Output (to memory):** `epics` — tree of `epic → stories → tasks`
- **Tools used:** `claude_tool`
- **Failure mode:** if decomposition fails, the orchestrator wraps stories in a single fallback "Uncategorized" epic so the output isn't empty. Audit log captures the failure.

### Gap Detector Agent
- **Input:** `stories`, `constraints`, `existing_tickets`
- **Output (to memory):** `duplicates`, `conflicts`, `gaps`
- **Tools used:** `claude_tool`, vector index via `memory.store`, and (in real integrations) `jira_tool` / `github_tool`
- **Failure mode:** if it fails, the user still gets epics+stories+tasks but no duplicate/conflict/gap analysis. The audit log captures the failure.

## Memory handoff contract

Every agent reads from and writes to `MemoryStore` via well-known keys:

| Key | Written by | Read by |
|---|---|---|
| `topics` | Parser | Story Writer |
| `constraints` | Constraint Extractor | Story Writer, Gap Detector |
| `stories` | Story Writer | Epic Decomposer, Gap Detector |
| `epics` | Epic Decomposer | Output Formatter |
| `existing_tickets` | Orchestrator (seeded from `--backlog`) | Gap Detector |
| `duplicates`, `conflicts`, `gaps` | Gap Detector | Output Formatter |
| `summary` | Parser | Output Formatter |

This explicit contract makes it easy to plug a new agent into the pipeline — declare what keys it reads and writes, and the orchestrator can sequence it correctly.

## Why not one prompt with a chain-of-thought structure?

A reasonable alternative would be a single prompt that does everything but asks the model to think step-by-step ("first list the topics, then write stories, then group into epics, then find duplicates"). We considered this and rejected it:

- **Token budget pressure.** Even with the latest Claude Sonnet, a single prompt covering all five steps approaches the output token limit. Some step inevitably gets compressed.
- **No memory between steps.** The model can't reach back to "what did I decide in step 2" — it has to re-derive everything within one continuous response.
- **No tool calls between steps.** The Gap Detector needs to *search* the existing JIRA tickets. A single-prompt design can only see what's in its context window.
- **No partial recovery.** If the model fumbles step 4, you've lost steps 1-3 too.

The multi-agent pipeline solves all four problems for the cost of one extra round-trip per stage.

## Where this design could go next

- **Parallel agent execution** where dependencies allow — Parser and Constraint Extractor are independent and could run in parallel. We chose sequential for now because runtime savings are negligible (~3s) and the audit log is cleaner.
- **Loops for refinement** — the Story Writer could re-run on stories the Gap Detector flagged as having weak AC. This adds non-determinism, so it's deferred.
- **Tool-use API** instead of prompt-only — Anthropic's tool-use feature could replace the JSON-fence parsing with structured outputs. Reduces the `_extract_json_block` risk surface.
- **Different models per agent** — Parser and Constraint Extractor could use Haiku (cheaper, faster); Story Writer and Gap Detector need Sonnet's reasoning. Easy to wire because each agent owns its own `ClaudeTool` instance.
