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
- **Output (to memory):** `stories` — list of full story records with `acceptance_criteria`, `priority`, `tags`, `source_topic_id`, `potential_constraint_conflicts`, and an `evidence` block attached deterministically by the agent from the cited topic (not produced by the model)
- **Tools used:** `claude_tool`
- **Failure mode:** if topics are empty, agent is skipped. If the LLM call fails permanently, the pipeline produces no stories — but topics and constraints are still preserved in memory and the audit log.

### Epic Decomposer Agent
- **Input:** `stories` from memory
- **Output (to memory):** `epics` — tree of `epic → stories → tasks`, every story field preserved verbatim with a `tasks[]` array added (`{id, title, type}`)
- **Tools used:** `claude_tool`
- **Failure mode:** if decomposition fails permanently, the failure is recorded in the audit log and the run returns whatever earlier stages produced (topics, constraints, stories) — the downstream Gap Detector is skipped because it reads `stories` that were never grouped.

### Gap Detector Agent
- **Input:** `stories`, `constraints`, `existing_tickets`
- **Output (to memory):**
  - `duplicates` — `{story_id, existing_id, confidence, reason, _similarity?}`, found by **local embeddings** (`sentence-transformers`, no LLM call)
  - `conflicts` — `{story_id, with, severity, reason}`, judged by the LLM against `must`/`forbidden` constraints
  - `gaps` — `{id (G-NN), title, description, related_ids, evidence}`, judged by the LLM
- **Tools used:** `claude_tool` (conflicts + gaps), `embedding_tool` (duplicates), top-K retrieval via `memory.store`, `jira_tool` / `github_tool` (live candidate search)
- **Hybrid by design:** duplicate detection is a similarity problem (embeddings, ~$0, deterministic); conflict/gap detection is a reasoning problem (LLM). Each sub-task uses the right tool.
- **Failure mode:** if the LLM call fails, the user still gets epics+stories+tasks (and embeddings-based duplicates) but no conflict/gap analysis. The audit log captures it.

### Cross-cutting: resilience & evidence
- **Provider failover** (opt-in via the `auto_switch` toggle): if any agent's provider fails after retries, the orchestrator retries that stage on the other provider (Claude↔Gemini), logged as a `provider_failover` audit event + an amber ⚠ live-log line.
- **Story evidence is system-attached**, not model-produced — the Story Writer agent copies the cited topic's `raw_quote`/`speaker`/`sentiment` into each story's `evidence` block, so it can't be hallucinated.

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

## Where this design has gone — and could go next

**Shipped since the original design:**
- **Per-stage model selection** — done. Free / Balanced / Premium presets + a per-stage override pick Claude or Gemini per agent (`_build_tool_for_model`); Balanced spends Claude only on the Story Writer.
- **Provider failover** — done. A failed stage retries on the other provider (opt-in `auto_switch`).
- **Multimodal input** — done. Vision-capable models read whiteboard photos/screenshots; the Parser auto-switches to Claude when an image is attached.
- **Jira write-back** — done. `publish_synthesis()` creates Epic→Story→Sub-task in live Jira.

**Still ahead:**
- **Parallel agent execution** where dependencies allow — Parser and Constraint Extractor are independent and could run concurrently. Kept sequential for a cleaner audit trail (and the orchestrator/audit log aren't thread-safe yet).
- **Loops for refinement** — the Story Writer could re-run on stories the Gap Detector flagged with weak AC. Adds non-determinism, so deferred.
- **Tool-use / structured outputs** instead of prompt-only — would replace the JSON-fence parsing (`_extract_json_block`) with guaranteed-schema outputs.
- **Two-way Jira sync** — write-back currently creates issues; it doesn't reconcile later edits or update on re-run.
