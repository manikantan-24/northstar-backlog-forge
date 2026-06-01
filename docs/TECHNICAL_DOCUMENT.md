# Backlog Synthesizer — End-to-End Technical Document

A multi-agent AI system that turns customer meeting transcripts, architecture wikis, and existing backlogs into structured, audited engineering stories — with gap, conflict, and duplicate detection against the live work in flight.

> **How to read this document.** It is a complete walkthrough of the system for interview / review preparation — from a user clicking *Analyze* all the way to the rendered Markdown output. Every LLM call, the exact request and response over the wire, the data that flows between the five agents, the retrieval math, the prompt iterations, and the design rationale behind each decision are covered. All JSON shapes and token counts shown are lifted from a real committed run (`outputs/20260528_205305/` and `outputs/20260527_235829/`), not invented.

---

## Table of Contents

1. [**Project Overview**](#1-project-overview) — what it is, why it exists, who it's for
2. [**System Architecture**](#2-system-architecture) — the 5-agent pipeline, why 5, where AI vs. Python sits, tech stack
3. [**Setup & Installation**](#3-setup--installation) — prerequisites, `.env`, first run (CLI + UI)
4. [**End-to-End Flow Walkthrough**](#4-end-to-end-flow-walkthrough) — every step, with the real data that flows between agents
5. [**Claude API + Gemini Integration**](#5-claude-api--gemini-integration) — model presets, the request over the wire, parsing, retry, cost
6. [**RAG / Retrieval Layer**](#6-rag--retrieval-layer) — embeddings, cosine similarity, the 20-ticket threshold, the 0.6 dedup floor
7. [**Prompt Engineering**](#7-prompt-engineering) — six prompts, four principles, six iteration cycles + the V2 generation
8. [**Web UI Internals**](#8-web-ui-internals) — Streamlit, dark dashboard, state, live progress, compare-mode
9. [**Testing Strategy**](#9-testing-strategy) — 128 tests, mocked LLM pattern, 10 golden cases, A/B, regression dashboard
10. [**Tracing and Auditability**](#10-tracing-and-auditability) — the audit log, three layers of provenance
11. [**Jira / Confluence Live Integration**](#11-jira--confluence-live-integration) — live pulls, one token for both products, seeding
12. [**Error Handling**](#12-error-handling) — the error hierarchy, graceful degradation, what's not handled
13. [**How AI Was Used to Build This**](#13-how-ai-was-used-to-build-this) — four development modes across the SDLC
14. [**Sample Interview Questions**](#14-sample-interview-questions-with-prepared-answers) — with prepared answers
15. [**What I'd Build Next**](#15-what-id-build-next) — the prioritized roadmap
- [**Appendix — Glossary**](#appendix--glossary)
- [**Appendix A — Elevator-Pitch Ladder**](#appendix-a--elevator-pitch-ladder) — 30s / 2min / deep-dive
- [**Appendix B — Data Model / Schema Reference**](#appendix-b--data-model--schema-reference) — every field of every artifact
- [**Appendix C — Defending the Evaluation Numbers**](#appendix-c--defending-the-evaluation-numbers) — 0.88 / 0.72, per-case breakdown, why the judge runs lower
- [**Appendix D — Single-prompt vs. 5-agent (the honest, measured comparison)**](#appendix-d--single-prompt-vs-5-agent-the-honest-measured-comparison)
- [**Appendix E — Memory Store Internals**](#appendix-e--memory-store-internals) — KV + vector, the content-addressed cache
- [**Appendix F — The Full Guardrail Catalog**](#appendix-f--the-full-guardrail-catalog) — all eight finding codes + severities
- [**Appendix G — Determinism & Concurrency**](#appendix-g--determinism--concurrency)
- [**Appendix H — Security & Privacy Posture**](#appendix-h--security--privacy-posture) — what redaction catches and misses
- [**Appendix I — Latency Breakdown**](#appendix-i--latency-breakdown) — where the ~50 seconds go
- [**Appendix J — Code Map**](#appendix-j--code-map) — one line per file
- [**Appendix K — Scoping & Approach**](#appendix-k--scoping--approach-why-this-why-not-the-others) — why this approach, why not the alternatives
- [**Appendix L — Why these LLMs**](#appendix-l--why-these-llms-and-not-others) — model choices and the rejected options
- [**Appendix M — Adding a new model / provider**](#appendix-m--adding-a-new-model--provider-exact-changes) — the exact files to change
- [**Appendix N — Code-review walkthrough**](#appendix-n--code-review-walkthrough-interview-prep) — every module explained, for interview prep

---

## 1. Project Overview

### What it is

**Backlog Synthesizer** is a five-agent pipeline that ingests three unstructured inputs (a meeting transcript, an architecture-constraints wiki, and a ticket backlog) and produces a structured engineering deliverable: epics → user stories with Given/When/Then acceptance criteria → tasks, plus a list of gaps, conflicts, and duplicates against the existing backlog. Every LLM call is captured in a reviewable audit trail.

### Why it exists

Engineering teams burn hours every sprint converting raw meeting notes into JIRA tickets. The process is mechanical (same translation every time), inconsistent across PMs (different shapes of stories, different priority systems, different tagging), and lossy (good ideas in customer calls never make it into the backlog because nobody had time to write them up). A single-prompt LLM solution works for short notes but breaks when the input also includes architecture constraints (offline mode is forbidden by PCI) and 50 existing tickets (don't refile NS-412 — it's already in progress). This system addresses all three concerns in one bounded pipeline.

### Who it's for

- **Product managers** running customer-discovery meetings who need clean tickets the next day
- **Engineering leads** doing sprint planning who need to see which new asks conflict with existing constraints
- **Architecture reviewers** auditing how the LLM reached its conclusions — every story traces back to a transcript quote, every conflict references the wiki rule
- **Submission reviewers (graders)** evaluating a multi-agent AI project for the Accenture "Accelerating Engineering Through AI-First Agentic Solutions" track

### Project at a glance

| Dimension | Value |
|---|---|
| Agents | 5 (Parser, Constraint Extractor, Story Writer, Epic Decomposer, Gap Detector) |
| LLM calls per run | 5 (one per agent; Gap Detector also runs local embeddings) |
| Golden eval cases | 10 (including negative / hallucination-resistance cases) |
| Test count | 128 (unit + agent + orchestrator + redactor + guardrails + compare-mode + live-Jira + live-Confluence + vision + eval runner) |
| Lines of production code | ~3,500 in `src/`, ~1,800 in `app.py` |
| External integrations | Anthropic Claude, Google Gemini, Jira Cloud REST, Confluence Cloud REST |

---

## 2. System Architecture

### 2.1 The 5-agent pipeline

```
Transcript ──┐
             ├─→ [01 Parser] ─→ topics ──┐
Wiki page ───┴─→ [02 Constraint Extractor] ─→ constraints ──┐
                                                            ├─→ [03 Story Writer] ─→ stories
                                                            │            │
Backlog ─────────────────────────────────────────────────────┤            ▼
                                                            │   [04 Epic Decomposer]
                                                            │            │
                                                            │            ▼
                                                            └─→ [05 Gap Detector]
                                                                         │
                                                                         ▼
                                            { epics, stories, tasks, gaps, conflicts, duplicates }
```

Each agent has exactly one reasoning task. Agents communicate through a shared `MemoryStore` (KV + vector layers) — they never call each other directly. The orchestrator (`src/orchestrator.py`) sets the order, handles per-stage failures, and aggregates the result. See [architecture.md](../architecture.md) for the formal Mermaid diagram.

#### Agent 1 — Parser

- **Reads:** raw transcript text (and any vision attachments)
- **Writes to memory:** `topics` (list of `{id, theme, summary, raw_quote, speaker, sentiment}` dicts) + a short `summary`
- **Single job:** identify the distinct asks / complaints / observations in the source, without yet turning them into stories
- **Prompt:** [prompts/parser_prompt.md](../prompts/parser_prompt.md)
- **Code:** [src/agents/parser_agent.py](../src/agents/parser_agent.py)

#### Agent 2 — Constraint Extractor

- **Reads:** wiki / architecture page text (file or live Confluence page)
- **Writes to memory:** `constraints` (list of `{id, severity, category, statement, source_excerpt, applies_to}` dicts)
- **Single job:** extract the engineering rules — required integrations, performance budgets, security/compliance rules, explicitly forbidden approaches — that downstream story-writing must respect
- **Prompt:** [prompts/constraint_extractor_prompt.md](../prompts/constraint_extractor_prompt.md)
- **Code:** [src/agents/constraint_agent.py](../src/agents/constraint_agent.py)

#### Agent 3 — Story Writer

- **Reads:** `topics` and `constraints` from memory
- **Writes to memory:** `stories` (list of `{id, title, description, user_story, acceptance_criteria, priority, priority_rationale, tags, source_topic_id, evidence, potential_constraint_conflicts}` dicts)
- **Single job:** draft a well-formed user story (with Given/When/Then acceptance criteria, priority, and rationale) for every topic — and flag stories that would conflict with a `must` / `forbidden` constraint instead of suppressing them
- **Prompt:** [prompts/story_writer_prompt.md](../prompts/story_writer_prompt.md)
- **Code:** [src/agents/story_writer_agent.py](../src/agents/story_writer_agent.py)
- **Post-processing:** attaches `evidence` blocks (linking each story back to a transcript quote)

#### Agent 4 — Epic Decomposer

- **Reads:** `stories` from memory
- **Writes to memory:** `epics` (list of `{id, title, description, stories: [...]}` where each story now also has `tasks: [{title, type}]`)
- **Single job:** group stories into themed epics and break every story into 3-7 concrete implementation tasks an engineer could pick up directly
- **Prompt:** [prompts/epic_decomposer_prompt.md](../prompts/epic_decomposer_prompt.md)
- **Code:** [src/agents/epic_decomposer_agent.py](../src/agents/epic_decomposer_agent.py)

#### Agent 5 — Gap Detector

- **Reads:** `stories`, `constraints`, `existing_tickets` from memory
- **Writes to memory:** `duplicates`, `conflicts`, `gaps`
- **Single job:** compare new stories against the existing backlog (find duplicates), against constraints (find conflicts), and against the source material (find gaps the conversation implied but nobody wrote a story for)
- **Prompt:** [prompts/gap_detector_prompt.md](../prompts/gap_detector_prompt.md)
- **Code:** [src/agents/gap_detector_agent.py](../src/agents/gap_detector_agent.py)
- **Hybrid stage:** duplicates are found via local sentence-transformers embeddings (no LLM call); conflicts + gaps go through one Claude/Gemini call

---

### 2.2 Why 5 agents — not 1, 2, or 3

This is the design question most reviewers ask first. The short version: 5 is the smallest number that gives each agent exactly **one reasoning task**. Below 5, agents start having to juggle two or more decisions in one prompt; above 5, you pay extra LLM calls for marginal separation gains.

#### Why not 1 agent (single mega-prompt)?

We considered a single mega-prompt — one call that takes the transcript + wiki + backlog and emits the entire synthesis in one shot — and rejected it for the reasons below. (Note on terminology: the bundled `versions/v1_baseline/` is **not** this single-prompt design; it is an earlier *multi-agent* snapshot that predates the later features. The genuine single-prompt comparison lives in `evaluation/single_prompt_baseline.py` — see Appendix D.) Three problems with the one-prompt approach:

1. **Quality is comparable on small inputs — but degrades on the cross-referencing tasks, and collapses at scale.** We measured this honestly (Appendix D): a single mega-prompt scores 0.84 deterministic vs the pipeline's 0.88 on the 10 golden cases — close. Where the single prompt actually breaks down is **duplicate detection against the backlog** (`case_06`: 0.33 vs 0.83), because it has to hold the entire backlog in context and reason over it inline rather than offloading to embeddings. That gap *widens* as the backlog grows — a single prompt can't fit hundreds of tickets, where retrieve-then-rerank still works. So the honest claim is "comparable drafting quality, decisive structural + dedup + scaling advantages," not "mediocre on every dimension."
2. **No intermediate state to inspect.** When something goes wrong (the model invents a duplicate that doesn't exist), there's no "topics" or "constraints" artefact to diff against. The audit trail is a black box: one prompt in, one synthesis out.
3. **No way to recover from partial failure.** If the response gets truncated, you re-run the whole thing. With 5 agents, only the failed stage re-runs.

#### Why not 2 agents (Extractor + Synthesizer)?

A natural compromise: "extract structured stuff from the input, then synthesise the backlog." Fails because:

- The Synthesizer still has to handle architecture constraints AND existing backlog matching AND priority reasoning AND task decomposition in one prompt. Same focus problem as the 1-agent case.
- You can't separate "draft this story" from "check whether it conflicts" — those are different cognitive operations. The model writes the story first, then has to read it back and judge it. Doing that in one pass produces worse judgments than separating the steps.

#### Why not 3 agents?

The first design we sketched was 3 agents: **Parser → Story Writer → Gap Detector**. Two missing pieces surfaced quickly:

- **No Constraint Extractor.** When the wiki says "card sales offline are FORBIDDEN by PCI" and a customer asks for offline card sales, the Story Writer either has to also extract that rule (mixing tasks) or the Gap Detector has to derive it from raw wiki text (terrible LLM economics — every conflict check re-parses the entire wiki). Splitting constraints into their own agent made the system 10x easier to reason about. Constraints get IDs (`C-04`), stories reference them, the audit log can prove which constraint fired.
- **No Epic Decomposer.** Grouping stories by theme is a different kind of reasoning from drafting them. We tried having the Story Writer also produce epics and got "1 epic per story" or "1 mega-epic with everything in it." Neither is useful. A dedicated agent that gets to see all the stories at once produces real thematic grouping — and gets to break each story into tasks, which the Story Writer simply doesn't have room to do well.

#### Why not 6, 7, or more?

We considered splitting Gap Detector into three (Duplicates, Conflicts, Gaps) and splitting Story Writer into (Story Drafter, AC Refiner). Both ideas got rejected:

- **Splitting Gap Detector** adds LLM calls without quality gain. The three judgments share enough context (the new story, the constraints, the candidate existing tickets) that doing them in one call is genuinely cheaper and just as accurate. We DID split out the duplicate detection — but locally, via embeddings, NOT as another LLM call.
- **Splitting Story Writer** sounded clean but didn't help in practice. The AC refinement step couldn't actually improve AC quality without seeing the topic again, at which point we'd just re-run the Story Writer.

5 is where the marginal LLM cost stops buying meaningful separation. It's the bounded pipeline's natural size.

---

### 2.3 Where AI sits, where deterministic Python sits

This separation is what makes the system reproducible and auditable. The model decides WHAT, deterministic Python decides HOW everything else flows.

#### AI does (≈5 LLM calls per run)

| Job | Where |
|---|---|
| Identify distinct topics in a transcript | Parser agent (1 Claude/Gemini call) |
| Extract architectural rules from a wiki | Constraint Extractor agent (1 call) |
| Draft user stories with acceptance criteria and rationale | Story Writer agent (1 call) |
| Group stories into epics + break each into tasks | Epic Decomposer agent (1 call) |
| Judge conflicts and gaps against constraints + backlog | Gap Detector agent (1 call) |
| Qualitative grading of the synthesis (optional) | LLM-as-judge in `evaluation/llm_as_judge.py` |
| Vision input understanding (optional) | Claude when whiteboard photos are attached |

That's 5 calls in a normal run. Compare-mode doubles it (running with two providers in parallel); LLM-as-judge adds 1 more per case during evaluation. Everything else is deterministic.

#### Deterministic Python does (everything else)

| Job | Where |
|---|---|
| Sequence the agents, handle per-stage failure, skip downstream stages when inputs are missing | `src/orchestrator.py` |
| Assign deterministic IDs (`T-01`, `C-01`, `ST-01`, `EP-01`) to LLM outputs | Each agent, after the LLM call |
| Validate / parse the model's JSON output (with fenced-block fallback) | `ClaudeTool._extract_json_block` |
| Retry on transient API errors with exponential backoff | `tenacity` decorators in `src/tools/claude_tool.py` |
| KV memory + vector indexing | `src/memory/store.py` |
| Cosine-similarity duplicate detection (the Gap Detector sub-step) | `src/tools/embedding_tool.py` (sentence-transformers + numpy) |
| Top-K candidate retrieval for the LLM | `MemoryStore.search_similar(query, top_k=5)` |
| Append-only audit logging | `src/memory/audit_log.py` |
| Post-LLM guardrails (6 deterministic checks: AC count, GWT grammar, unique titles, canonical tags, story grounding, priority-rationale rigor) | `src/guardrails.py` |
| PII redaction (regex over emails, phones, SSNs, card numbers, names) | `src/redactor.py` |
| Strict-redact halt-on-violation at the LLM trust boundary | `redactor.assert_redacted` + orchestrator |
| Token usage aggregation and per-stage cost computation | `_aggregate_token_usage` + `src/pricing.py` |
| Loading transcripts (txt / md / PDF), wiki text, ticket JSON | `src/input_loader.py` |
| Jira REST pagination + ADF→text adapter | `src/tools/jira_tool.py` (live mode) |
| Confluence REST + HTML-storage stripper + markdown-to-storage converter | `src/tools/confluence_tool.py` (live mode) |
| Output formatting (JSON + Markdown) | `src/output_formatter.py` |
| Eval suite: deterministic metric computation | `evaluation/metrics.py` |
| Eval suite: regression dashboard | `evaluation/dashboard.py` |
| A/B prompt comparison | `evaluation/ab_compare.py` |
| Streamlit UI rendering, state management, run history | `app.py`, `src/ui/styling.py` |
| 128 tests, all using mocked LLM responses | `tests/` |

The pattern inside every agent is the same: **deterministic Python prepares the prompt → AI produces structured JSON → deterministic Python validates, assigns IDs, writes to memory, and records the audit event.** The model never decides what to do next or whether to call another agent — that's the orchestrator's job.

#### Why this split matters

- **Reproducibility.** Re-running with the same inputs and the same model produces the same orchestration, the same retry behaviour, the same memory shape, and the same audit trail. Only the LLM output can vary, and that variance is bounded by the prompt's strict JSON schema.
- **Testability.** 128 tests run with mocked LLM responses because the deterministic layers are the same in test and in production.
- **Auditability.** Every Python step writes an audit event. Reviewers can trace each story back through deterministic IDs to a specific LLM call, prompt, and response.
- **Cost-bounded.** 5 LLM calls per run is a hard ceiling we set in code, not a soft heuristic.

---

### 2.4 Technology stack

| Layer | Technology | Why |
|---|---|---|
| **LLM providers** | Anthropic Claude Sonnet 4.5 (primary); Google Gemini 2.5 Flash (Free + Balanced presets) | Multi-provider so cost vs. quality is a user choice. Sonnet for the hardest reasoning (Story Writer); Flash for mechanical stages. Claude vision-capable for whiteboard photos. |
| **LLM SDKs** | `anthropic` (≥0.39), `google-genai` (≥1.0) | Official SDKs. Both lazy-imported so the test suite runs without either installed. Note: `google-genai` is the new SDK, **not** the deprecated `google-generativeai`. |
| **Retry / backoff** | `tenacity` (≥8.2) | Exponential backoff with jitter on `RateLimitError` and `APIConnectionError`. Caps at 3 attempts; auth/400 errors are raised immediately rather than retried. |
| **Multi-agent infrastructure** | Custom (`src/orchestrator.py`, `src/agents/`, `src/memory/`) | We didn't reach for LangGraph or CrewAI. Hand-rolling kept the design surface small, the audit semantics clear, and the dependency footprint at ~10 packages. |
| **Local embeddings** | `sentence-transformers` (`all-MiniLM-L6-v2`), `numpy` | 384-dim dense vectors; ~80 MB model; runs on CPU or Apple Silicon (MPS). Cosine similarity is one numpy matmul. No API spend. |
| **Vector store (current)** | In-process numpy index + optional file-backed cache (`MEMORY_PERSISTENT=1` → `.cache/memory/vectors/<hash>.npz`) | Lightweight for demo, persistent if you want it. Cache key includes the model name + corpus hash so it invalidates correctly. |
| **Vector store (production target)** | `chromadb` (≥0.4.22) — listed in `requirements.txt`, not yet wired | The `MemoryStore` interface is shaped for the swap; deferred until multi-host deployment is on the table. |
| **PDF input** | `pypdf` (≥4.0) | Text-extractable PDFs only; scanned/image PDFs error with a clear "needs OCR" message rather than silently dropping content. |
| **PII redaction** | Pure regex (`src/redactor.py`) | Deterministic, audit-friendly, no extra model dependency. Conservative on personal names (low false-positive rate, accepts some false negatives). |
| **External REST** | `requests` (≥2.31) | One dep covers Jira REST v3 (`/rest/api/3/search/jql`, `/rest/api/3/issue`) and Confluence REST v2 (`/wiki/api/v2/pages`). We didn't pull in the official Atlassian SDK — heavy for two endpoints. |
| **HTTP mocking (tests)** | `monkeypatch.setattr(requests, "get", ...)` (pytest built-in) | No `responses` or `httpx-mock` dep needed. Tests run offline. |
| **UI framework** | `streamlit` (≥1.32) | Single-file UI, server-side rendering, native Python. 1.32 is the floor because `st.dialog` (modal helpers) lands there. |
| **UI charts** | `pandas` (≥2.0) | Used only by the cost-panel bar chart + cost-trend line chart. Optional — the table-only fallback works without it. |
| **Config / secrets** | `python-dotenv` (≥1.0) + plain env vars | `.env` for local development; production reads from the deployment platform's secret store. |
| **Test framework** | `pytest` (≥7.4) | 128 tests in 9 files. Average runtime ~1 second per file. Zero API spend in CI. |
| **CI** | GitHub Actions | Unit tests on every push; ruff lint (narrow ruleset — pyflakes + syntax); Docker-build verification; gated eval suite on `workflow_dispatch` so it never spends API credit unintentionally. |
| **Container** | Docker (multi-stage, Python 3.11-slim, non-root user, CPU-only PyTorch) | The image is around 1 GB (mostly sentence-transformers + torch). Healthcheck on `/_stcore/health` makes it Kubernetes-ready. |
| **Logging** | Python's `logging` module via `src/logger_setup.py` | One JSON-friendly formatter, env-controlled verbosity (`LOG_LEVEL=INFO` by default). |
| **Linting** | `ruff` (in CI only) | Pyflakes + E9 syntax rule. We don't bikeshed style in PRs. |

#### What's deliberately NOT in the stack

- **No agent framework** (LangGraph, AutoGen, CrewAI). We considered them. The orchestration logic is ~150 lines of plain Python and gives us precise control over retry, audit, and failure semantics. Framework adoption would have added a dependency we'd outgrow.
- **No vector DB at runtime** (Pinecone, Weaviate, Qdrant). 50-200 tickets per run; numpy is already faster than the network round-trip a cloud vector DB would add.
- **No queue / scheduler** (Celery, RQ). Synthesis is interactive (≤60s); background workers are overkill for v1.
- **No frontend build pipeline** (Next.js, React). Streamlit's server-rendered components are good enough for the dashboard and skip the JS/TS toolchain entirely.

---

## 3. Setup & Installation

### Prerequisites

- **Python 3.9 – 3.12** (tested on 3.9 and 3.11)
- **Anthropic API key** at minimum; `GOOGLE_API_KEY` optional (only needed for Gemini presets)
- **Atlassian API token** optional (only needed if you want live Jira / Confluence pulls)
- ~100 MB free disk space (for the sentence-transformers model on first run)

### One-time setup

```bash
git clone <repo-url> backlog-synthesizer
cd backlog-synthesizer

python -m venv venv
source venv/bin/activate         # macOS / Linux
# .\venv\Scripts\activate        # Windows

pip install -r requirements.txt
cp .env.example .env
$EDITOR .env                     # paste your ANTHROPIC_API_KEY
```

### `.env` reference

| Variable | Required when | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | Premium / Balanced presets | Account-level key from <https://console.anthropic.com/> |
| `GOOGLE_API_KEY` | Free / Balanced presets | Free tier at <https://aistudio.google.com/apikey> |
| `JIRA_BASE_URL` | Live Jira mode | e.g. `https://your-tenant.atlassian.net` |
| `JIRA_EMAIL` / `JIRA_API_TOKEN` | Live Jira mode | Atlassian API token (<https://id.atlassian.com/manage-profile/security/api-tokens>) |
| `JIRA_PROJECT_KEY` | Live Jira mode | Default project (e.g. `NS`) |
| `CONFLUENCE_*` | Live Confluence mode | Auto-populated from `JIRA_*` at startup since both products share one token |
| `MEMORY_PERSISTENT` | Optional speedup | `1` to cache embeddings on disk between runs |
| `ANTHROPIC_MODEL` | Optional | Default `claude-sonnet-4-5` |

### First run (CLI)

```bash
python src/main.py \
  --transcript samples/meeting_notes.txt \
  --constraints samples/architecture_constraints.md \
  --backlog samples/jira_backlog.json
```

Output lands in `outputs/<UTC timestamp>/`:
```
outputs/20260528_201622/
├── synthesis.json     # structured output: epics, stories, tasks, gaps...
├── synthesis.md       # human-readable Markdown version
└── audit_trail.md     # chronological agent decisions with reasoning + token counts
```

### First run (UI)

```bash
streamlit run app.py
# Opens http://localhost:8501
```

The sidebar bundles the same three inputs as the CLI plus options for live Jira / Confluence, model presets, PII redaction, vision attachments, and compare-mode. The pipeline runs visibly stage-by-stage.

### Verifying everything works

```bash
pytest tests/ -v
```

Expected output: `128 passed`. The suite is mocked end-to-end and costs zero API credit.

---

## 4. End-to-End Flow Walkthrough

This section traces a single run from CLI invocation to written output — and, unlike a narrative, it shows the **actual data that moves between agents** at each step. The values below are taken from the committed `outputs/20260528_205305/` run (a pharmacy-refill transcript against a live 46-ticket Jira backlog, Balanced preset). Where that run produced empty results, the `outputs/20260527_235829/` run (4 duplicates, 1 conflict, 4 gaps) is used instead — noted inline.

### The shape of one run, at a glance

```
                    transcript_text (3,593 chars)        constraint_text (5,600 chars)        existing_tickets (46)
                            │                                    │                                   │
                   [01 Parser] LLM                      [02 Constraint Extractor] LLM               │
                            │                                    │                                   │
              memory["topics"] (3) ───────┐        memory["constraints"] (11) ──────┐               │
              memory["summary"]           │                                         │               │
                                          ▼                                         ▼               │
                                   [03 Story Writer] LLM  ◀── reads topics + constraints            │
                                          │                                                         │
                              memory["stories"] (3, each + evidence)                                │
                                          │                                                         │
                                          ▼                                                         │
                                   [04 Epic Decomposer] LLM  ◀── reads stories                      │
                                          │                                                         │
                              memory["epics"] (3, stories now carry tasks[])                        │
                                          │                                                         │
                                          ▼                                                         ▼
                                   [05 Gap Detector]  embeddings (dupes, no LLM) + 1 LLM call (conflicts, gaps)
                                          │
                          memory["duplicates"], ["conflicts"], ["gaps"]
                                          │
                                          ▼
                            guardrails (6 checks) → result dict → synthesis.json / synthesis.md / audit_trail.md
```

Five LLM calls, one local-embedding pass, six deterministic guardrail checks. Total wall time on this run: ~50s (Balanced preset; Gemini Flash for four stages, Claude Sonnet for the Story Writer).

### Step 1 — CLI parses arguments and loads `.env`

`src/main.py` calls `load_dotenv()` first thing so `ANTHROPIC_API_KEY` is available. It then auto-promotes `JIRA_*` env vars into `CONFLUENCE_*` so one Atlassian token covers both products. The CLI supports:
- File-based inputs (`--transcript`, `--constraints`, `--backlog`)
- Live overrides (`--confluence-page-id 65830`, `--live-jira`)
- Vision attachments (`--vision-image whiteboard.png`, repeatable)
- Privacy (`--redact-pii`)
- Dry-run mode (build prompts but don't call the LLM)

### Step 2 — Inputs are loaded

`src/input_loader.py` handles four input types:
- `.txt` / `.md` → UTF-8 read with Latin-1 fallback
- `.pdf` → `pypdf.PdfReader` walks pages, joins extracted text with `--- Page N ---` separators
- `.json` (tickets) → normalised into `{id, title, description, status, labels, raw}` regardless of whether the source is JIRA-style (`key`) or GitHub-style (`number`)

Live-source flags skip the file loader; the orchestrator fetches them itself at Step 4.

### Step 3 — Orchestrator initialises

`Orchestrator()` in `src/orchestrator.py` instantiates the per-stage tools lazily (the LLM tool is chosen per stage from the user's model preset). A fresh `MemoryStore` + `AuditLog` are created — both are scoped to this single run. No state leaks between runs.

### Step 4 — Live fetches (if requested)

When `live_confluence_page_id` or `live_jira=True` is set, the orchestrator hits the REST APIs **before** any LLM call. Success and failure events are written to the audit log immediately so the run is reproducible:
- `live_confluence_fetch_ok` payload: `{page_id, chars_fetched}`
- `live_confluence_fetch_failed` payload: `{page_id, error}`

### Step 5 — Optional PII redaction

If `redact_pii=True`, the orchestrator runs `redact()` over transcript / constraints / backlog before any agent sees them. Emails, phones, SSNs, card numbers, and conservatively-matched personal names become stable placeholders (`[EMAIL_1]`, `[NAME_3]`). The same `RedactionMap` is shared across all three inputs so identical values map to the same token — that consistency lets the Gap Detector match a redacted name in a story back to the same redacted name in an existing ticket.

If `strict_redact=True`, the orchestrator then re-scans every input at the LLM boundary. If any pattern slipped through, it raises `StrictRedactionViolation` and halts. Failure is audit-logged.

### Step 6 — Stage 1: Parser runs

The Parser agent reads `transcript_text` (and any vision attachments). It substitutes the text into `{{TRANSCRIPT}}` in `prompts/parser_prompt.md`, calls the LLM (`call_for_json`, `max_tokens=4000`), parses the JSON response, assigns deterministic `T-XX` ids to each topic, and writes `topics` + `summary` to `MemoryStore`.

**What goes in** — a 3,593-char transcript, expanded into a 5,275-char prompt after template substitution.

**What comes back** — `1,578` input tokens / `511` output tokens. The model returns `{summary, topics}`; the agent stamps `T-01…T-NN` ids on each topic. After this stage `memory["topics"]` holds (abridged to one of three):

```json
{
  "theme": "patient-scoped-notifications",
  "summary": "The current notification system sends alerts to the household account rather than the specific patient, leading to family members receiving notifications for others' prescriptions...",
  "raw_quote": "The Rx service knows which prescription belongs to which person, but the notification system fires to the account, not the patient. [...] The push goes only to the device that's logged in as that specific patient identity through NSID.",
  "speaker": "Marcus",
  "sentiment": "concern",
  "id": "T-02"
}
```

The `raw_quote` field is the linchpin of the audit story: it is a verbatim lift from the transcript that every downstream story will cite as `evidence`. Audit events: `parser / started` → `parser / tool_call` (captures the full prompt + response + usage) → `parser / completed`.

### Step 7 — Stage 2: Constraint Extractor runs

Reads `constraint_text` (file or live Confluence — 5,600 chars on this run). One LLM call (`{{WIKI_CONTENT}}` substituted, `max_tokens=4000`, `1,951` in / `1,156` out). Assigns deterministic `C-XX` ids. Writes `constraints` to memory. This stage runs **in parallel-of-concern** with the Parser — it depends only on the wiki, not on the topics — but the orchestrator runs it sequentially for a simpler audit trail.

After this stage `memory["constraints"]` holds 11 rules. Each carries a `severity` (`must` / `should` / `forbidden`), a `category`, the rule `statement`, the verbatim `source_excerpt` it was derived from, and `applies_to` tags. Two examples that matter for the rest of the run:

```json
[
  {
    "id": "C-05", "severity": "must", "category": "compliance",
    "statement": "Pharmacy refill notifications must be HIPAA-compliant, requiring opt-in stored on the prescription, sending to the patient's verified contact (not household default), and retaining an audit log for 7 years.",
    "source_excerpt": "HIPAA-compliant notifications only: opt-in stored on the prescription, sent to the patient's verified contact (not household default), audit log retained 7 years.",
    "applies_to": ["pharmacy", "mobile-app"]
  },
  {
    "id": "C-06", "severity": "forbidden", "category": "compliance",
    "statement": "Pricing must not be personalized based on inventory state without explicit legal disclosure.",
    "source_excerpt": "Pricing must not be personalized based on inventory state without disclosure (Legal).",
    "applies_to": ["mobile-app", "ecommerce"]
  }
]
```

`C-05` is what the Story Writer will reference when it drafts the notification story; `C-06` (`forbidden`) is the kind of rule the Gap Detector checks new stories against to raise a `conflict`.

### Step 8 — Stage 3: Story Writer runs

Reads `topics` and `constraints` from memory and serializes both into the prompt (`{{TOPICS_JSON}}`, `{{CONSTRAINTS_JSON}}`). This is the only stage that uses `max_tokens=8000` — stories with full Given/When/Then acceptance criteria are the longest output in the pipeline, and a 4000 cap truncated them mid-JSON during testing. On the Balanced preset this is the one stage that runs on **Claude Sonnet 4.5** (`2,705` in / `1,442` out) — it does the hardest reasoning.

The prompt enforces strict rules: Rule 1 — draft a story for EVERY topic; Rule 2 — never suppress a story because it conflicts with a constraint (draft it and flag the conflict instead); Rule 3 — `priority_rationale` must be concrete, not "it's important."

**Post-processing (deterministic).** After the LLM returns, the agent does two things in Python: (1) stamps `ST-01…ST-NN` ids, and (2) for each story, looks up its `source_topic_id` in the topics map and copies the topic's `raw_quote` / `speaker` / `sentiment` into a new `evidence` block. As of the V2 prompts (§7), the Story Writer prompt no longer asks the model to produce `evidence` at all — it is fully system-owned, which means it can never be hallucinated; the model's only traceability job is to set `source_topic_id` accurately. After this stage `memory["stories"]` holds full story dicts:

```json
{
  "id": "ST-01",
  "title": "Enable proactive push notifications for prescription ready status",
  "description": "...HIPAA compliance requires explicit patient opt-in stored on the prescription record and delivery only to the patient's verified contact method... Constraint C-05 mandates opt-in, verified contact delivery, and 7-year audit retention.",
  "user_story": "As a pharmacy patient, I want to receive a push notification on my mobile device when my prescription is ready for pickup, so that I can plan my store visit without opening the app.",
  "acceptance_criteria": [
    "Given a prescription status changes to 'ready-for-pickup' in Rx Hub, when the patient has opted in, then a push notification is sent to the patient's verified device within 60 seconds.",
    "Given a patient has not opted in, when that prescription becomes ready, then no push notification is sent.",
    "Given a push notification is sent, when delivered, then an audit log entry is written containing timestamp, patient ID, prescription ID, delivery method, and status."
  ],
  "priority": "High",
  "priority_rationale": "...",
  "tags": ["pharmacy", "mobile-app", "notifications"],
  "source_topic_id": "T-01",
  "potential_constraint_conflicts": [...],
  "evidence": [
    {"topic_id": "T-01", "theme": "proactive-refill-notifications",
     "raw_quote": "a proactive push notification when a prescription is ready...",
     "speaker": "Anika", "sentiment": "concern"}
  ]
}
```

Note the story explicitly references `C-05` in its own description and acceptance criteria — the Story Writer was given the constraints as context, and it threads them through. Writes `stories` to memory.

### Step 9 — Stage 4: Epic Decomposer runs

Reads `stories` from memory (`{{STORIES_JSON}}`, `max_tokens=8000`; on the `20260527_235829` run this stage processed 5 stories with `3,232` in / `3,724` out and produced 5 epics / 32 tasks). It groups stories into themed epics and breaks each story into 3-7 concrete implementation tasks. The prompt explicitly requires preservation of every story field verbatim (`priority_rationale`, `source_topic_id`, `evidence`, `potential_constraint_conflicts`) — a real bug-fix after we observed the model silently dropping them (see §7).

After this stage `memory["epics"]` holds epics whose `stories[]` are the same story dicts as before, now each carrying a `tasks[]` array with deterministic `ST-NN-TK-NN` ids:

```json
{
  "id": "EP-01",
  "title": "HIPAA-Compliant Pharmacy Notifications",
  "description": "...",
  "stories": [
    {
      "id": "ST-01", "title": "Enable proactive push notifications...",
      "...": "all original fields preserved verbatim",
      "tasks": [
        {"id": "ST-01-TK-01", "type": "backend",
         "title": "Design and implement Rx Hub API and data model for per-prescription patient push notification opt-in."},
        {"id": "ST-01-TK-02", "type": "backend",
         "title": "Modify Rx Hub 'prescription ready' event publisher to include opt-in status and verified patient contact methods."}
      ]
    }
  ]
}
```

Writes `epics` to memory. This is the only place the story dicts get nested under epics — everything before this point is a flat `stories` list.

### Step 10 — Stage 5: Gap Detector runs

Reads `stories`, `constraints`, and `existing_tickets` from memory. Hybrid mode:
- **Local embeddings** (`EmbeddingTool` wrapping sentence-transformers) compute cosine similarity between each new story and every existing ticket. Matches above the 0.6 threshold become `duplicates`. **No LLM call for this sub-step.**
- **Vector retrieval + LLM call.** For each new story, the top-5 most similar existing tickets are fetched from the vector index and passed to Claude. The LLM produces `conflicts` (vs. architectural constraints) and `gaps` (capabilities implied by the source but absent from both new stories and existing backlog).

**What the slimming + retrieval looks like.** The Gap Detector does not send full story bodies. It builds `slim_stories` (`{id, title, description}` triples) and, for each one, queries the vector index for the top-5 most similar existing tickets (`candidates_per_story`). On a 46-ticket backlog the prompt that results is large but bounded — the `20260527_235829` run sent a `22,364`-char prompt and used `6,102` input / `962` output tokens. Note the asymmetry: lots of input (candidates), little output (just the judgments).

After this stage memory holds three arrays. Real output from the `20260527_235829` run:

```json
// memory["duplicates"]  (from local embeddings — no LLM)
{ "story_id": "ST-01", "existing_id": "#1156", "confidence": "high",
  "reason": "Both describe enabling offline cash sales and returns under $50 during WAN outages at POS, including local SKU/pricing cache and the same card-payment-online-only requirement." }

// memory["conflicts"]  (from the LLM, judged against constraints)
{ "story_id": "ST-02", "with": "C-05", "severity": "medium",
  "reason": "Story proposes integrating inventory into search ranking, which could be interpreted as price personalization based on inventory state; description doesn't specify a disclosure mechanism." }

// memory["gaps"]  (from the LLM, implied-but-unwritten work; V2 shape)
{ "id": "G-01",
  "title": "Offline transaction reconciliation after WAN recovery",
  "description": "ST-01 and #1156 enable offline cash transactions but neither addresses how these sync back once connectivity is restored, which is critical for inventory accuracy, financial reporting, and fraud detection.",
  "related_ids": ["ST-01"],
  "evidence": "ST-01 describes local SKU pricing validation but no reconciliation mechanism; #1156 mentions SQLite cache refresh but no bidirectional sync after outage." }
```

The `duplicates` carry an `existing_id` that points into the real backlog (`#1156` is a GitHub-style id; live Jira would show `NS-47`). The `conflicts` reference a constraint id (`C-05`). The `gaps` are the most interesting output — they are work nobody drafted a story for but the source material implies. Writes `duplicates`, `conflicts`, `gaps` to memory.

### Step 11 — Post-LLM guardrails

`src/guardrails.py` runs six deterministic checks against the assembled output:
1. AC count in range (2-7)
2. Acceptance criteria use Given/When/Then keywords
3. Story titles are unique within the run
4. Tags come from the canonical NorthStar vocabulary
5. Every story traces to a parsed topic (`source_topic_id` valid)
6. High-priority stories have a substantive `priority_rationale`

Findings are non-blocking — they ride along on the result dict as `guardrail_findings` and surface in the UI's Guardrails tab. The tally is also audit-logged. On the pharmacy run, the guardrails produced three `info`-level findings (the most common one in practice):

```json
[
  {"code": "non_canonical_tag", "severity": "info", "story_id": "ST-01",
   "message": "Tags outside the canonical set: ['notifications']. Either add them to the vocabulary or normalise."},
  {"code": "non_canonical_tag", "severity": "info", "story_id": "ST-03",
   "message": "Tags outside the canonical set: ['sms', 'notifications']. Either add them to the vocabulary or normalise."}
]
```

These are deliberately `info`, not `error`: a drifting tag is worth surfacing to a reviewer but should never block a synthesis. An `error`-severity finding (e.g. a story with zero acceptance criteria, or one whose `source_topic_id` doesn't resolve) is the signal that something actually went wrong.

### Step 12 — Result assembly and persistence

The orchestrator builds the final result dict:
```python
{
    "summary": ...,
    "topics": ...,
    "constraints": ...,
    "epics": [...],
    "gaps": [...],
    "conflicts": [...],
    "duplicates": [...],
    "guardrail_findings": [...],
    "audit_trail": "<markdown>",
    "token_usage": {agent: {input, output}, total: {...}},
    "model": "<summary string>",
    "models": {stage: model_id, ...},
}
```

If `redact_pii=True`, the final synthesis is un-redacted on the way out so the user sees real names; the audit log stays redacted on purpose (it's the artefact a compliance reviewer would inspect).

### Step 13 — Writing files

`output_formatter.write_outputs` writes `synthesis.json` and `synthesis.md` to `outputs/<timestamp>/`. The orchestrator writes `audit_trail.md` separately. Total runtime on the bundled sample: ~30-60 seconds with Sonnet 4.5, ~15-25 with Gemini Flash.

---

## 5. Claude API + Gemini Integration

### Model choice

The system supports per-stage model selection. Three presets ship:

| Preset | Parser | Constraint | Story Writer | Epic Decomposer | Gap Detector |
|---|---|---|---|---|---|
| **Free** | gemini-2.5-flash | gemini-2.5-flash | gemini-2.5-flash | gemini-2.5-flash | gemini-2.5-flash |
| **Balanced** (default) | gemini-2.5-flash | gemini-2.5-flash | **claude-sonnet-4-5** | gemini-2.5-flash | gemini-2.5-flash |
| **Premium** | claude-sonnet-4-5 | claude-sonnet-4-5 | claude-sonnet-4-5 | claude-sonnet-4-5 | claude-sonnet-4-5 |

Reasoning: the Story Writer benefits the most from the stronger model (it does the hardest reasoning — synthesising stories from raw topics while respecting constraints). The other agents are mechanical enough that Gemini Flash handles them well at a fraction of the cost. The sidebar "Advanced" expander lets users override any stage individually.

### Request shape — Claude

`src/tools/claude_tool.py`:
```python
self._client.messages.create(
    model=self.model,                  # e.g. "claude-sonnet-4-5"
    max_tokens=max_tokens,              # 4000 or 8000 per agent
    system=self.system_prompt,          # loaded from prompts/system_prompt.md
    messages=[{"role": "user", "content": content}],
)
```

Where `content` is either a plain string (text-only) or a multimodal list when vision attachments are present:
```python
content = [
    {"type": "image", "source": {"type": "base64", "media_type": img.media_type, "data": img.data_b64}},
    # ... more images ...
    {"type": "text", "text": user_message},
]
```

### What the request looks like over the wire

The SDK call above serializes to a single `POST` against the Messages API. For the Parser stage of the pharmacy run:

```http
POST https://api.anthropic.com/v1/messages
x-api-key: sk-ant-api03-...
anthropic-version: 2023-06-01
content-type: application/json

{
  "model": "claude-sonnet-4-5",
  "max_tokens": 4000,
  "system": "You are an experienced agile delivery lead embedded in NorthStar Retail's engineering org. You turn unstructured inputs into clean, well-formed backlog artefacts. Be precise, structured, and conservative: never invent work that isn't grounded in the source...",
  "messages": [
    { "role": "user",
      "content": "You will be given a raw meeting transcript. Extract the distinct **topics** — coherent asks, complaints, or observations...\n\n<transcript>\nPharmacy refill escalation — ...\n</transcript>\n\nReply with JSON only." }
  ]
}
```

And the response (truncated):

```http
HTTP/1.1 200 OK
content-type: application/json

{
  "id": "msg_01...",
  "type": "message",
  "role": "assistant",
  "model": "claude-sonnet-4-5",
  "content": [
    { "type": "text",
      "text": "{\n  \"summary\": \"Pharmacy refill experience is generating significant customer complaints...\",\n  \"topics\": [ { \"theme\": \"proactive-refill-notifications\", ... } ]\n}" }
  ],
  "stop_reason": "end_turn",
  "usage": { "input_tokens": 1578, "output_tokens": 511 }
}
```

Three things worth noting: (1) the `system` parameter carries the role and global rules and is **identical across all five agents** — only the user `content` changes per stage; (2) the model is asked for "JSON only," and `_extract_json_block` (below) cleans up the times it doesn't comply; (3) `usage.input_tokens` / `output_tokens` are exactly the numbers that flow into the audit log and the cost panel. The Gemini wire format differs (it's the `google-genai` `generate_content` shape), but `GeminiTool.call_for_json` normalizes the return to the same `(parsed_dict, usage)` tuple so no agent code knows the difference.

### Response parsing

Models occasionally wrap JSON in markdown fences (` ```json ... ``` `) or add a leading sentence. `ClaudeTool._extract_json_block` defensively tries three strategies in order:
1. A fenced ` ```json ... ``` ` block (regex)
2. The first balanced `{ ... }` substring (regex)
3. Raise `ToolError` with the first 300 chars of the response

This keeps the agents resilient to minor prompt-following lapses without forcing them to retry on every cosmetic deviation.

### Retry logic

Each `_call_with_retry` is decorated with `@retry(retry=retry_if_exception_type((RateLimitError, APIConnectionError)), stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))`. Other errors (auth, bad request, etc.) raise `ToolError` immediately so the orchestrator can mark the stage as failed without burning the budget on retries that can't possibly succeed.

### Gemini integration

`src/tools/gemini_tool.py` uses the new `google-genai` SDK (NOT the deprecated `google-generativeai`). The interface mirrors `ClaudeTool.call_for_json` so the orchestrator's `_build_tool_for_model` can build either based on the model id prefix (`claude-*` → ClaudeTool, `gemini-*` → GeminiTool). No agent code knows or cares which provider it's using.

### Provider failover + vision auto-switch (`auto_switch`)

`Orchestrator.run(auto_switch=...)` adds two opt-in resilience behaviours, controlled by the sidebar **"Auto-switch model"** toggle (default **off** so the exact preset is honoured and easy to verify):

- **Provider failover.** If a stage's provider fails after its tenacity retries (rate limit, 5xx, timeout), the orchestrator retries that one stage on the *other* provider (`_fallback_model`: Claude↔Gemini). This is what keeps a live demo — or a compare-mode "Free" (all-Gemini) leg — from dying on a transient outage. Every switch emits a distinct `failover` progress event (rendered as an amber **⚠ FAILOVER** line) and a `provider_failover` audit event recording `from → to` + the error.
- **Vision auto-switch.** The Gemini wrapper can't carry image parts, so when a vision attachment is present and the Parser is on a Gemini model, the Parser is switched to `claude-sonnet-4-5` — otherwise the image would be silently dropped. Logged, never silent.

Both are deliberately gated: with the toggle off, a failing stage stops at `✗ failed` (predictable); with it on, failures become recoveries and are surfaced in the live log, a toast, an end-of-run summary, and a persistent banner. Failover is also skipped when a test injects a fake tool, so the mocked suite is unaffected.

### Cost & token tracking

After each call, the agent records `record_tool_call(..., usage={"input_tokens": X, "output_tokens": Y})`. The orchestrator's `_aggregate_token_usage` walks the audit events at the end of the run and builds a `{agent: {input, output}, total: {...}}` dict. The UI's cost panel applies the per-stage model rate from `src/pricing.py` to produce a per-agent USD breakdown.

### Cost per run (real numbers)

These are the actual token counts from the committed `outputs/20260528_205305/` run (Balanced preset — Gemini Flash for four stages, Claude Sonnet for the Story Writer). The Gap Detector LLM call failed on this particular run, so its row is filled from the comparable `20260527_235829` run for completeness.

| Stage | Model | Input tokens | Output tokens | Notes |
|---|---|---:|---:|---|
| Parser | gemini-2.5-flash | 1,578 | 511 | `max_tokens=4000` |
| Constraint Extractor | gemini-2.5-flash | 1,951 | 1,156 | 11 constraints extracted |
| Story Writer | **claude-sonnet-4-5** | 2,705 | 1,442 | `max_tokens=8000`, hardest reasoning |
| Epic Decomposer | gemini-2.5-flash | 2,543 | 2,741 | output-heavy (tasks per story) |
| Gap Detector | gemini-2.5-flash | ~6,100 | ~960 | input-heavy (top-5 candidates per story) |
| **Total** | mixed | **~14,900** | **~6,800** | ~21K tokens/run |

Order-of-magnitude cost: on the **Premium** preset (all Sonnet, ~$3/1M input + $15/1M output) a run is roughly **$0.15** — call it 5-20¢. On **Free** (all Gemini Flash) it is effectively free at this volume. The Balanced preset sits in between, spending Sonnet money only on the one stage that benefits from it. A 50-run prompt-tuning session on Premium is ~$7.50; on Balanced, well under a dollar. This is why the eval suite is gated behind `workflow_dispatch` in CI — so it never spends credit unintentionally — and why all 128 tests mock the LLM.

---

## 6. RAG / Retrieval Layer

### Why retrieval at all

The Gap Detector compares each new story against every existing ticket in the backlog. With 50 tickets that's tractable for the LLM; with 500 it's not (cost + context window). Local embeddings narrow the comparison set to the top-5 semantically similar tickets per story before the LLM ever sees them.

### Embeddings

`src/tools/embedding_tool.py` wraps `sentence-transformers` with model `all-MiniLM-L6-v2`:
- 384-dimensional dense vectors
- Cosine-similarity-friendly (normalised at encode time)
- ~80 MB model, runs on CPU or Apple Silicon (MPS)
- Lazy-loaded on first use; cached for the rest of the process lifetime

### MemoryStore vector layer

`src/memory/store.py` exposes two methods:

```python
def index_tickets(self, tickets: list[dict]) -> bool:
    """Embed tickets if there are ≥ 20 of them. Returns True if indexed."""

def search_similar(self, query_text: str, top_k: int = 5) -> list[dict]:
    """Return the top-K most similar tickets, each tagged with `_similarity`."""
```

### The 20-ticket threshold

```python
_RETRIEVAL_THRESHOLD = 20
```

Below 20 tickets, the vector index is skipped entirely — `search_similar` returns the full list, and the LLM sees everything. Above 20, the index is built and only the top-5 candidates per story go to the LLM. The threshold is set so small demo backlogs don't pay the embedding cost; production backlogs (hundreds to thousands of tickets) get the bounded prompt size.

### Duplicate detection (the local-only path)

`EmbeddingTool.find_duplicates(stories, existing_tickets, threshold=0.6)` runs cosine similarity between every new story and every existing ticket. Matches above the threshold are recorded as duplicates with a `_similarity` score. **This path makes no LLM call at all** — it's pure linear algebra. Cheap, deterministic, fast.

### Threshold tuning

The threshold defaults to 0.6 — chosen after measuring that thematically clear matches (e.g. "Pharmacy refill SMS reminder" vs "Notify customer when prescription is ready") scored 0.62-0.70 on `all-MiniLM-L6-v2`. The original 0.75 default was leaving real duplicates on the table.

False positives at 0.6 are tolerable because the Gap Detector LLM downstream still gets to reject them on the conflict + gap call. False negatives at 0.75 were the bigger problem — a missed duplicate becomes a re-filed ticket in production.

### Persistent vector store (optional)

`MEMORY_PERSISTENT=1` enables file-backed caching:
- `.cache/memory/vectors/<corpus_hash>.npz` — embedded vectors keyed by a SHA-256 of the ticket corpus + model name
- `.cache/memory/kv/<key>.json` — KV state from each `put()`

Re-runs on the same backlog skip the embed step (which costs ~5-10 seconds for 50 tickets on Apple Silicon). The cache invalidates automatically when the corpus changes — the hash includes every ticket's title + description.

For production scale, swap the file-backed layer for ChromaDB (already in `requirements.txt`); the `MemoryStore` interface is shaped so this is a one-class change.

---

## 7. Prompt Engineering

### Six prompts, one shared role

Every agent loads `prompts/system_prompt.md` as the system message, then its own per-agent template as the user message with placeholders replaced. The system prompt sets the role ("agile delivery lead embedded in NorthStar Retail"), the conservatism principles ("don't invent, don't pad"), and the JSON-only output contract.

Per-agent prompts:
| Agent | Prompt | Placeholders |
|---|---|---|
| Parser | `parser_prompt.md` | `{{TRANSCRIPT}}` |
| Constraint Extractor | `constraint_extractor_prompt.md` | `{{WIKI_CONTENT}}` |
| Story Writer | `story_writer_prompt.md` | `{{TOPICS_JSON}}`, `{{CONSTRAINTS_JSON}}` |
| Epic Decomposer | `epic_decomposer_prompt.md` | `{{STORIES_JSON}}` |
| Gap Detector | `gap_detector_prompt.md` | `{{NEW_STORIES_JSON}}`, `{{CANDIDATES_JSON}}`, `{{CONSTRAINTS_JSON}}` |

### Design principles

**Principle 1 — One task per prompt.** No prompt asks the model to do two reasoning tasks. The Story Writer drafts stories; it does NOT also group them into epics. Composability + testability.

**Principle 2 — Schema as specification.** Every prompt includes an exact JSON example showing the output shape. Models are very good at imitating schema examples; richer placeholder values (e.g. `"priority_rationale": "ALWAYS POPULATE. Example for High: ..."`) yield better real outputs than terse field descriptions.

**Principle 3 — Rules over instructions.** Numbered rule lists ("1. Draft a story for EVERY topic", "2. Never suppress on conflict") outperform paragraph-style guidance. The model treats each rule as a hard constraint.

**Principle 4 — Handle ambiguity explicitly.** When the source is unclear, the prompt instructs the model to flag the ambiguity in the description rather than guess. Conservative-by-default reduces hallucination.

### Six iteration cycles (the prompt-engineering audit)

| Iteration | Trigger | Fix |
|---|---|---|
| 1. AC schemas were too loose | Stories came back with prose acceptance criteria | Schema enforced Given/When/Then format string |
| 2. Tag vocabulary drifted across runs | Same story tagged `pos` one run, `point-of-sale` next | Canonical tag set added to Story Writer prompt; non-canonical tags now an info-level guardrail finding |
| 3. Duplicate detection bled into two stages | Story Writer and Gap Detector both produced "potential duplicates" lists | Story Writer flags "potential" via `potential_constraint_conflicts`; Gap Detector emits the final `duplicates` array. Clear separation. |
| 4. Epic Decomposer created 1 epic per story | Defeated the purpose of grouping | Added "epics are themes, not buckets" rule; story-to-epic ratio improved |
| 5. Story Writer suppressed on conflict-heavy meetings | Conflict-heavy transcripts produced zero stories (case_07 in the eval) | Added explicit "never suppress — draft + flag" rule with a concrete example. |
| 6. **Parser dropped "blocked" requests** (the real case_07 root cause) | Even with rule 5, case_07 still produced zero stories — because the *Parser* returned zero topics. Its "skip topics the team said no to" rule was filtering out requests that were *blocked by a constraint* (PCI), so the Story Writer never saw them. | Split "declined" (chose not to pursue → skip) from "blocked" (requested but forbidden → **keep**, so the conflict can be flagged). Verified: case_07 went **0.33 → 1.00 deterministic, 0.00 → 0.70 judge**. Full write-up in [PROMPT_ENGINEERING.md](PROMPT_ENGINEERING.md) iteration 6. |

Each iteration was observed in a real run (or a golden-eval case) before the rule was added. We don't add rules speculatively.

### Recent prompt fix worth highlighting

The Epic Decomposer's JSON schema originally listed `id, title, description, user_story, acceptance_criteria, priority, tags, tasks` — but omitted `priority_rationale`, `source_topic_id`, `evidence`, and `potential_constraint_conflicts`. The model dutifully copied only the listed fields, silently breaking audit traceability and downstream conflict detection. The eval suite caught it: the judge consistently said "priority rationale is missing." Fix: added the audit-required fields to the schema with `(REQUIRED: verbatim from input)` markers, plus a new rule requiring every story field to pass through unchanged.

### Prompts V2 — the current generation (what changed and why)

After the iteration history above, all six prompts were rewritten to a tighter V2 generation. The guiding goal: keep every prompt the strongest version *and* perfectly consistent with the deterministic code that parses it — so the schema a prompt declares is exactly what the agents, renderers, and tests expect. Crucially, **all of V2 lives in the prompts; zero code changes were required.**

Per-prompt improvements:

- **System prompt** — added an *enum-exactness* rule ("use enumerated labels exactly as specified; don't change casing, invent synonyms, or add values"), an explicit field-passthrough/traceability rule, precise definitions of `conflict` and `gap`, and a "don't invent implementation detail" rule. These target failure modes observed in real runs.
- **Parser** — the declined-vs-blocked distinction (iteration 6 above), plus dominant-sentiment tie-breaking, merge-repeated-mentions, and order-by-importance.
- **Constraint Extractor** — explicit modal-verb→severity mapping (`must`/`required`/`shall` → `must`; `should`/`prefer` → `should`; `do not`/`never`/`prohibited` → `forbidden`), compound-statement splitting, de-duplication, and conditional-logic preservation. This is the most-improved prompt.
- **Story Writer** — a hard "every topic id must appear as a `source_topic_id`" grounding guarantee, narrowest-reasonable-persona inference, and a re-added worked example (the blocked-offline case that pins down "draft + flag, never suppress").
- **Epic Decomposer** — minimum-epics-that-preserve-meaning rule, concrete task-type definitions, a "don't restate acceptance criteria as tasks" rule, and `spike`-only-when-justified discipline.
- **Gap Detector** — now scoped to **conflicts + gaps only**; structured gaps carry `id`, `related_ids`, and a string `evidence` sentence; added a worked conflict+gap example.

Cross-cutting reconciliations (the prompt↔code consistency pass):

| Decision | Why — grounded in the code |
|---|---|
| **Unified on a single `id` field** across every artifact (dropped `topic_id`/`constraint_id`/`epic_id`) | The agents assign `id` themselves ([constraint_agent.py:57](../src/agents/constraint_agent.py#L57), [epic_decomposer_agent.py:61](../src/agents/epic_decomposer_agent.py#L61)) and nothing reads the model's separate id fields. The old scheme produced a confusing dual id on epics (`E-01` from the model vs `EP-01` from Python). |
| **Story `evidence` is no longer in the model's output schema** | [story_writer_agent.py](../src/agents/story_writer_agent.py) overwrites it anyway, attaching `{topic_id, theme, raw_quote, speaker, sentiment}` from the cited topic. V2 documents it as system-attached — which also means **evidence can't be hallucinated**. This supersedes the old "split-contract" worry. |
| **Gap `evidence` kept as a string sentence** (not a list of objects) | The renderers [output_formatter.py:90](../src/output_formatter.py#L90) and [app.py:808](../app.py#L808) string-interpolate it — a list would render raw Python dicts into `synthesis.md` and the UI. |
| **`source_topic_ids` → `related_ids` on gaps** | The Gap Detector never receives the original topics (only new stories, candidate tickets, and constraints), so it can only cite story/ticket ids. A real run confirmed the field gets `ST-` ids; the old name was a misnomer a reviewer would catch. |
| **Duplicates removed from the Gap Detector prompt** | Duplicates are detected by local embeddings (`use_embeddings_for_duplicates=True`), so asking the LLM for them wasted tokens on discarded output. This supersedes iteration 3's arrangement, where the LLM emitted the `duplicates` array. |

Because the mocked tests select each stage's canned response by matching a substring of the prompt, the test fixtures were re-anchored to stable, unique phrases in the new prompts (e.g. `extract the distinct topics`, `Duplicate detection is handled separately`) — which also makes them less brittle to future wording tweaks. Verified after the overhaul: **128 tests pass**, and a real end-to-end run on the conflict-heavy case produced 2 epics / 4 stories / 4 conflicts / 3 gaps with clean gap rendering, unified ids, and correctly system-attached story evidence.

---

## 8. Web UI Internals

### Streamlit choice

The UI is a single `app.py` (~1,800 lines) running on Streamlit 1.32+. We picked Streamlit because:
- Native Python — no JS/TS toolchain
- Hot reload on file save
- Built-in widgets for file upload, multi-select, data tables
- Server-side rendering — no need to mock the backend for the UI to work

### Dark dashboard styling

`src/ui/styling.py` ships a single ~1,000-line CSS payload injected once per Streamlit rerun via `st.markdown(get_css(), unsafe_allow_html=True)`. Theme values live in CSS custom properties:

```css
:root {
    --bg: #0a0e1a;
    --bg-elev-1: #11162a;
    --accent: #22d3ee;        /* cyan */
    --violet: #a78bfa;
    --green: #34d399;
    --amber: #fbbf24;
    --rose: #fb7185;
}
```

Component CSS is split into logical modules (`_HEADER_CSS`, `_PIPELINE_CSS`, `_KPI_CSS`, `_EMPTY_CSS`, `_STORY_CSS`, `_FINDING_CSS`, `_RUN_META_CSS`, `_NEXT_CSS`, `_DUP_DIFF_CSS`, `_HISTORY_CSS`) and concatenated at render time. Streamlit's default chrome (top toolbar, status widget, deploy button) is hidden but the sidebar collapse chevron is explicitly kept visible — Streamlit's modern toolbar embeds the chevron inside the header, and hiding the whole header left users with no way to expand a collapsed sidebar.

### State management

Streamlit re-runs the entire script on every interaction, so state lives in `st.session_state`:

| Key | Purpose |
|---|---|
| `result` | The last completed orchestrator output dict |
| `run_dir` | Path to the most recent `outputs/<ts>/` |
| `stage_states` | `["idle", "active", "done", "failed", "skipped"]` per stage |
| `tokens_total`, `cost_usd`, `model_used` | Run-meta strip values |
| `token_usage` | Per-agent input/output token counts |
| `models` | Active per-stage model dict |
| `active_preset` | Currently active model preset |
| `existing_tickets_cache` | Backlog cache for the duplicate-compare modal |

UI selections (`transcript_choice`, `constraints_choice`, etc.) are also persisted to disk in `.streamlit/user-state.json` so a hard browser reload preserves them.

### Live progress streaming

The orchestrator accepts a `progress_callback(stage_index, stage_name, event, detail)` callable. The UI's run handler defines one that:
1. Updates `stage_states[stage_index]` to "active" / "done" / "failed" / "skipped"
2. Tracks per-stage start time and computes elapsed seconds on completion
3. Re-renders the pipeline placeholder with new states
4. Updates a progress strip with `STAGE_NAME · event · 12.3s`

Streamlit's `st.empty()` placeholders are used so the pipeline and progress strip rewrite in place rather than appending.

### Compare-mode

When the sidebar "Compare providers" toggle is on, the run handler calls `orch.run_compare(primary_models, secondary_models, ...)` which runs the pipeline twice sequentially and assembles a `comparison` dict with side-by-side metrics + deltas. The UI renders a violet-bordered comparison banner showing each metric for both providers, the delta, and a story-title overlap percentage.

### Run history dialog

`show_run_history_dialog()` opens a Streamlit dialog listing past runs (one JSON per run under `logs/runs/`). Improvements in this submission build over a v1: aggregate strip at the top (total runs / stories drafted / cumulative cost), search input, date-bucket grouping (Today / Yesterday / This week / Older), per-row delete, and a "current run" badge for whichever run is loaded.

### Audit trail rendering

`with tab_audit: st.markdown(result.get("audit_trail", "..."), unsafe_allow_html=True)`. The `unsafe_allow_html=True` is required because the audit log uses `<details>` / `<summary>` blocks to make the full LLM prompt + response collapsible per event. Reviewers can expand any agent's call and see exactly what was sent and received.

### Input pickers — multi-select sources + always-visible upload

Each source picker (transcript, wiki, backlog) is an `st.multiselect`, so a user can combine **several bundled samples at once** — transcripts/wikis are concatenated with `===== filename =====` separators; backlogs are merged into one ticket list. Beneath each picker is an always-visible **"Upload your own"** expander (`accept_multiple_files=True`); uploaded files are combined with the selected samples by `_resolve_text` / `_resolve_tickets`. The vision picker additionally lists a bundled **whiteboard sample** (`samples/whiteboard_sprint_planning.png`) selectable without an upload. (Earlier versions gated uploads behind an "Upload my own…" dropdown option, which hid the dropzone behind the open popover — the always-visible expander fixed that.)

### Top-right navigation

The header carries an adaptive nav: **Home · History · Help** always, plus **Export · Create-in-Jira** once a result exists. Home clears the run back to the start screen; Help opens a "How it works" dialog (the five-agent overview); Export and Create-in-Jira open dialogs reusing the download and `publish_synthesis` paths.

### Accumulating live log

The per-stage `progress_callback` events are **appended** to a running log (rather than overwriting a single placeholder), so each agent's line persists as the next stage runs — `▸ started · ✓ completed · ✗ failed · – skipped`, each with elapsed seconds, plus an amber **⚠ FAILOVER** line when `auto_switch` retries a stage on the other provider. An end-of-run summary line, a `st.toast`, and a persistent banner under the KPI cards make any failure or failover impossible to miss (the audit trail holds the durable detail).

### Branding

The sidebar leads with an Accenture wordmark (brand purple `#A100FF`) + an "AI-First Agentic Solutions" eyebrow, and closes with a footer noting the demo runs on **mock data for a fictional client (NorthStar Retail)** with live Atlassian optional. The Streamlit `primaryColor` is the dashboard cyan; multi-select chips are styled as subtle dark pills rather than the default bright fill.

---

## 9. Testing Strategy

### Test breakdown (128 total)

| Suite | Tests | Coverage |
|---|---|---|
| `test_agents.py` | 11 | Per-agent unit tests with mocked Claude tool |
| `test_orchestrator.py` | 3 | End-to-end pipeline with mocked Claude |
| `test_redactor.py` | 10 | PII patterns, name blocklist, round-trip |
| `test_guardrails.py` | 20 | All 6 guardrail checks + severity + edge cases |
| `test_compare_mode.py` | 8 | run_compare shape, deltas, leg-prefix callbacks |
| `test_jira_live.py` | 23 | JQL builder, pagination, ADF→text, 401/400/500 paths, **write-back** (create_issue, fallback, publish_synthesis, partial-failure) |
| `test_confluence_live.py` | 24 | REST endpoints, HTML entity decoding, md→storage converter |
| `test_vision.py` | 14 | VisionAttachment validation, multimodal Claude payload, parser forwarding |
| `test_evaluation_runner.py` | 15 | list_cases, run_case shape, judge failure handling, dashboard regression detection |

### Mocked-LLM testing pattern

Every test uses `FakeClaudeTool` (or a stage-aware `LegAwareFakeClaudeTool` for compare-mode) instead of the real SDK. The fake matches on prompt substrings to pick a canned response per stage:

```python
fake_claude = FakeClaudeTool({
    "extract the **distinct topics**": {"summary": "...", "topics": [...]},
    "extract the **architectural constraints**": {"constraints": [...]},
    "draft well-formed user stories": {"stories": [...]},
    # ... etc.
})
orch = Orchestrator(claude=fake_claude, jira=FakeJira(), ...)
```

This keeps the suite deterministic, fast (~1 second for all 128 tests), and zero-cost. No `pytest --skipif "no API key"` markers — tests run identically in CI, on a fresh laptop, and on a forked PR.

### HTTP mocking for live integrations

For Jira and Confluence live mode, tests patch `requests.get` / `requests.post` directly:

```python
def _patch_get(monkeypatch, responder):
    captured = []
    def _fake_get(url, *, params=None, auth=None, headers=None, timeout=None, **kw):
        captured.append((url, dict(params or {})))
        return responder(url, params or {}, **kw)
    monkeypatch.setattr(requests, "get", _fake_get)
    return captured
```

Lets us assert on the exact URL, JQL, and headers without ever touching the network.

### Golden evaluation suite

`evaluation/golden_dataset/` ships **10 hand-curated cases**:

| Case | Theme | Designed to test |
|---|---|---|
| 01 | NorthStar Q3 planning meeting | Baseline / happy path |
| 02 | Pharmacy refill escalation | Domain-specific synthesis |
| 03 | Mobile team Slack standup | Different document style |
| 04 | Customer support note (negative) | Hallucination resistance |
| 05 | Empty / cancelled meeting | Edge case — zero-story expected |
| 06 | All-duplicates | Every theme overlaps existing tickets |
| 07 | Conflict-heavy | Every ask blocked by constraints |
| 08 | Ambiguous customer panel | Heavy hedging, must extract conservatively |
| 09 | Multi-team planning | Cross-domain themes |
| 10 | Compliance review (HIPAA/GDPR/PCI) | Regulatory drivers must surface |

Each case has an `_input.json` (file pointers) and an `_expected.json` (assertions about minimum/maximum story count, required topics, forbidden topics, expected duplicates, expected conflicts). `evaluation/metrics.py` runs six deterministic scores per case; `evaluation/llm_as_judge.py` adds five qualitative dimensions (1-5, normalised to 0-1).

The most recent committed run is at `evaluation/results/20260601T061247Z/` — deterministic average **0.88**, LLM-judge average **0.72** across 10 cases. This run already includes the case_07 Parser fix (§7 iteration 6) and the V2 prompts; case_07 itself scores 1.00 deterministic / 0.90 judge (was 0.33 / 0.00). The earlier `20260528T154409Z/` run (0.80 / 0.53) is the pre-fix baseline, kept for comparison. See Appendix C for the per-case breakdown.

### A/B prompt experimentation

`evaluation/ab_compare.py` swaps a candidate prompt in, runs the suite, restores the original. Per-case deltas + verdict ("variant wins / control wins / tie"). Useful for "does this prompt tweak actually help?" decisions before committing.

### Regression dashboard

`evaluation/dashboard.py` walks every `evaluation/results/<ts>/summary.json`, sorts newest-first, and surfaces any case whose deterministic score dropped ≥ 0.10 vs. the previous run. Wired into the CI workflow so a prompt regression on `main` is visible in the workflow run artifacts.

---

## 10. Tracing and Auditability

### Why this is a first-class concern

The rubric explicitly requires "audit logs must show how conclusions were reached." Beyond that requirement, audit traceability is what makes the system *defensible* — a reviewer can pull up any synthesis and see exactly which transcript quote produced which story.

### `AuditLog` design

`src/memory/audit_log.py` is an append-only structured log scoped to a single run. Each event captures:

```python
@dataclass
class AuditEvent:
    timestamp: str    # ISO-8601 UTC
    agent: str        # "parser", "constraint", "story_writer", ...
    event: str        # "started", "completed", "failed", "tool_call", ...
    payload: dict     # structured event-specific data
    reasoning: str    # one-sentence human-readable explanation
```

The log lives in memory during the run and is serialised to `audit_trail.md` at the end. JSON access via `as_json_list()` for programmatic consumers.

### What's captured

Every agent emits at minimum two events: `started` and `completed` (or `failed` / `skipped`). The `tool_call` event captures the LLM interaction in detail:

```python
audit.record_tool_call(
    agent="parser",
    tool="claude",
    request={"prompt_chars": 12453, "max_tokens": 4000},
    response_excerpt="<300 char preview>",
    tokens_used=2150,
    usage={"input_tokens": 1875, "output_tokens": 275},
    prompt=<full prompt, up to 16 KB>,
    response_text=<full response JSON, up to 16 KB>,
)
```

The full prompt + full response are stored under `prompt_full` / `response_full` keys in the payload. The markdown renderer puts them inside collapsible `<details>` blocks so the trail reads as a high-level narrative by default, with the option to expand any agent and see exactly what was sent and what came back.

### Three layers of provenance

Every story carries enough metadata to trace it back to the source material:

1. **`source_topic_id: "T-03"`** → pointer into the topics array → which contains
2. **`raw_quote: "cashiers couldn't ring up customers"`** → a direct lift from the transcript → which is
3. **Captured in `evidence` block on every story** → so the UI's Epics tab shows it inline

A reviewer skeptical of any specific story can: click the story, see the evidence quote, search the transcript for that quote, confirm the reasoning. No black box.

### Audit events worth knowing

| Event | Recorded when | Why it matters |
|---|---|---|
| `pii_redacted` | Redaction ran | Compliance: shows the run was redacted before LLM exposure |
| `strict_redact_violation` | Strict redaction halted the run | Security: a leak signal that didn't reach the LLM |
| `live_confluence_fetch_ok` / `_failed` | Live wiki pull | Provenance: distinguishes a live-data run from a fixture run |
| `live_jira_fetch_ok` / `_failed` | Live backlog pull | Same |
| `indexed_tickets` | Vector index built | Shows the RAG path was active |
| `duplicates_detected_locally` | Embeddings ran the dedup | Shows the LLM didn't do duplicate work |
| `guardrails_completed` | Post-LLM checks ran | Carries the `{error, warn, info}` tally |
| `tool_call` | Any LLM invocation | The big one — full prompt + response |

### Persistence

Per-run files land under `outputs/<UTC timestamp>/audit_trail.md`. Each run's summary metadata also goes to `logs/runs/<timestamp>_<short-id>.json` for the history dialog. Outputs are intentionally not gitignored — they ship with the repo as evidence of past runs.

### What this is NOT (yet)

The audit log is append-only within a run but not tamper-evident across runs. Anyone with filesystem write access can edit `audit_trail.md` after the fact. [PRODUCTION_READINESS.md](../PRODUCTION_READINESS.md) lists this as a P1 item — production should write events to an immutable store (S3 with object lock, or a hash-chained log like Sigstore Rekor).

---

## 11. Jira / Confluence Live Integration

### What it does

The orchestrator can pull live data from your Atlassian tenant in two places:
- **Constraints from a Confluence page** — fetched via `GET /wiki/api/v2/pages/{id}`, body returned as storage-format XHTML, stripped to plain text, fed to the Constraint Extractor.
- **Existing backlog from a Jira project** — fetched via `GET /rest/api/3/search/jql` with JQL like `project = "NS" ORDER BY created DESC`, paginated, mapped to the internal ticket shape, fed to the Gap Detector.

### How it's wired

Mock vs. live is per-tool, controlled by `mode="mock"|"live"` on `ConfluenceTool` and `JiraTool` (or the `CONFLUENCE_MODE` / `JIRA_MODE` env vars). The CLI exposes `--confluence-page-id N` and `--live-jira` flags; the UI exposes a "Live Atlassian sources" sidebar expander. Either path eventually calls `orch.run(live_confluence_page_id=..., live_jira=True, ...)`.

When a live flag is set, the orchestrator fetches the data BEFORE any LLM call, then records the outcome in the audit trail as `live_confluence_fetch_ok` (with `chars_fetched`) or `live_jira_fetch_ok` (with `ticket_count`). Failures are non-blocking: the orchestrator falls back to whatever was passed in, and the user sees a warning in the UI.

### One token for both products

The app auto-promotes `JIRA_BASE_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN` into `CONFLUENCE_BASE_URL` / `CONFLUENCE_EMAIL` / `CONFLUENCE_API_TOKEN` at startup. Atlassian Cloud uses one API token per user that's valid for every product on the same tenant, so duplicating it in `.env` would be pure friction.

### Writing back to Confluence

`scripts/seed_confluence.py` reads the bundled `samples/architecture_constraints.md` and `samples/product_strategy.md`, converts markdown to Confluence storage XHTML via `markdown_to_confluence_storage()`, and creates two pages in the first non-personal space (or `--space DEMO` to pick one explicitly). Same auth as the read path. Idempotency is intentional but light: re-running produces 409s on existing pages which the script logs and continues past.

### Writing back to Jira

`scripts/seed_jira.py` pushes the bundled 30-ticket `samples/jira_backlog.json` into the configured `JIRA_PROJECT_KEY`. Each created issue gets a `backlog-synth-seed-v1` label so the script can detect already-seeded items on a re-run and skip them. Used to populate the demo Jira project with tickets that overlap the bundled transcript themes — without this, a live-Jira demo would show "0 duplicates" because the live project wouldn't have the matching tickets.

### Benefits

- **Realism in demos.** Reviewers see real Jira keys (NS-47, NS-48) instead of mocked IDs. The audit trail entries say "Pulled 76 ticket(s) from live Jira" — that's a believable enterprise scenario.
- **No fixture drift.** When the live backlog evolves, the system sees the evolution automatically. With a JSON fixture, the file gets stale within a sprint.
- **Provenance.** The audit trail distinguishes runs against live data from runs against fixtures — useful when a downstream PM asks "which JIRA snapshot did this synthesis come from?"
- **Same code path for demo and prod.** The agents don't know or care whether their inputs came from a file or a REST call. Tests pin the file path; deployments pin the live path.

### Write-back: create the synthesis in Jira

The loop is now closed end-to-end. `JiraTool.publish_synthesis(result)` walks the synthesized backlog and creates it in the live project as a real hierarchy:

- each epic → an **Epic** issue,
- each story → a **Story** issue, parented to its epic, with the user story, acceptance criteria, priority rationale, conflict flags, and task list rendered into the description,
- each task → a **Sub-task** under its story (best-effort).

It's defensive about project-config differences (`JiraTool.create_issue` progressively drops `parent`, then `labels`, then falls back to issue type `Task`), and partial failures are recorded so a single rejected issue doesn't abort the publish. Triggers:

- **CLI:** `python src/main.py … --publish-jira` (and `--no-jira-subtasks`), which prints the created Epic/Story keys + `…/browse/NS-xxx` links.
- **UI:** the **⤴ Jira** top-nav button / the "Create in Jira" dialog, which shows the created issues as clickable links.

The human is still the loop *before* the click — you review the synthesis, then publish — but the system does write real tickets. (Tested in `tests/test_jira_live.py` with mocked `requests.post`, including the fallback and partial-failure paths.)

### What it's still not

Not a two-way sync — it creates issues but doesn't reconcile edits made in Jira afterward, and it doesn't update issues on a re-run (a fresh publish creates fresh issues).

---

## 12. Error Handling

### Layered approach

Errors are handled at the layer closest to their cause, with a clear hierarchy:

```
ToolError       (per-tool failures — auth, HTTP, JSON parse, fixture missing)
   ↓
AgentError      (per-agent failures — wraps ToolError with agent-name context)
   ↓
Orchestrator    (per-stage handling — logs to audit, sets stage state, skips
                 downstream agents whose inputs are now missing)
   ↓
UI / CLI        (top-level — friendly error display, non-zero exit code)
```

### Error types and how they're surfaced

| Error | Cause | Surfaced as |
|---|---|---|
| `ToolError("ANTHROPIC_API_KEY isn't set")` | Missing env var | CLI: `[error] Orchestrator init failed` + exit code 1. UI: red banner inline. |
| `ToolError("Anthropic API error: ...")` | API auth failure, model unavailable | Audit event `agent / failure`, stage marked failed, downstream agents marked skipped |
| `RateLimitError` / `APIConnectionError` | Transient | Retried up to 3 times with exponential backoff (tenacity) before being raised as `ToolError` |
| `ToolError("Model produced invalid JSON: ...")` | LLM didn't follow schema | Same as API error — non-retried, stage fails, partial result still returned |
| `AgentError("Parser LLM call failed: ...")` | Any tool error inside an agent | Caught by orchestrator; recorded as `record_failure(agent, error)` |
| `InputError("File not found: ...")` | Bad CLI path | CLI: `[error] Could not read transcript`, exit code 2 |
| `InputError("Ticket file is not valid JSON")` | Malformed backlog | Same as above |
| `StrictRedactionViolation([findings])` | PII slipped past redactor | Run halted; audit event `strict_redact_violation` with kinds + first-context (NOT raw samples — those might be the PII) |
| `ToolError("Jira auth failed (401)")` | Bad token / no Confluence on tenant | `live_jira_fetch_failed` audit event, fallback to whatever ticket list was passed in |
| `ToolError("Confluence page not found")` | Bad page ID | Same pattern |
| `ToolError("Strict redaction violated")` | Halt-on-PII triggered | Run aborted, propagated up to CLI / UI with error message |
| Streamlit form callback exception | Bad user input | Streamlit's default red box plus a `_progress_placeholder.error(...)` line |
| Background process crash | Subprocess died (eval suite, seed scripts) | Captured stdout/stderr in `/tmp/`, surfaced via notification |

### Defensive design choices

- **Partial results are still useful.** If the Story Writer fails, the orchestrator still returns whatever the Parser + Constraint Extractor produced. The UI shows the failed stages in red but renders the available output. Better than throwing the run away.
- **Progress callbacks never break the pipeline.** UI hook errors are caught and logged but don't propagate. A bug in the rendering layer can't kill an in-flight synthesis.
- **Live-mode failures degrade gracefully.** Live Confluence/Jira failures don't halt the run; the audit log captures the failure and the run continues with whatever was already in scope (or an empty list).
- **No silent swallowing.** Every `except: pass` in the codebase has a comment explaining why it's safe in that specific context. Grep `# noqa: BLE001` for the exhaustive list.

### What's NOT handled

- **OOM on huge transcripts.** A 1 MB transcript would blow past Claude's context window. We don't pre-chunk. Currently this would manifest as an API error and be surfaced as a tool failure. [PRODUCTION_READINESS.md](../PRODUCTION_READINESS.md) lists chunking as a future item.
- **Concurrent writes to the audit log.** The orchestrator is single-threaded per run. Compare-mode runs two pipelines sequentially, not concurrently, partly because the audit log isn't thread-safe.
- **Network partition during a live fetch.** The retry policy covers transient errors but not a sustained outage. The run will fail one stage; the orchestrator continues with empty inputs.

---

## 13. How AI Was Used to Build This

The rubric explicitly requires documenting AI usage across the SDLC. The full version is in [docs/AI_USAGE_SDLC.md](AI_USAGE_SDLC.md); this is the technical reader's summary.

### Phase-by-phase AI usage

| SDLC phase | What AI was used for | Concrete artefacts |
|---|---|---|
| **Problem framing** | Claude as a thought partner — surfaced the need for an audit log (the original spec didn't mention it), pushed back on the "single autonomous agent" temptation, identified that conflicts include constraint contradictions (not just topic overlap). | Architecture decisions captured in `docs/AGENT_DESIGN.md` |
| **Design** | Claude as a design critic — flagged missing agents (the original 3-agent design had no Constraint Extractor; the model insisted that the conflicts story didn't work without one). | Five-agent design in `architecture.md` |
| **Prompt design** | Claude as a prompt critic + edge-case generator — asked "what would make a story prompt produce duplicates in two different agents?" → caught the design ambiguity between Story Writer's `potential_constraint_conflicts` and Gap Detector's `conflicts` arrays. | Six iteration cycles + the V2 prompt generation documented in `docs/PROMPT_ENGINEERING.md` |
| **Evaluation plan** | Claude as a test brainstormer — proposed metrics (completeness of AC, accuracy of feature tagging, F1 for conflict detection), suggested negative test cases (hallucination resistance, ambiguous customer panels). | Metrics in `evaluation/metrics.py`; 10 golden cases in `evaluation/golden_dataset/` |
| **Sample data** | Claude as a sample writer — generated the third revision of `meeting_notes.txt` (the first two were too clean; we asked for "messier with cross-references for overlap detection"). | `samples/meeting_notes.txt`, `samples/architecture_constraints.md` |
| **Implementation** | Claude as a coding pair — wrote the initial `MemoryStore`, the audit log schema, and the test fixtures. Code reviewed every PR by the human author. | `src/memory/`, most of `tests/` |
| **Documentation** | Claude for drafting, human for review — every doc in this repo was AI-drafted and human-edited. | This file, README, AGENT_DESIGN, PROMPT_ENGINEERING, AI_USAGE_SDLC, PRODUCTION_READINESS, CHANGELOG |

### Where AI was NOT used

- **Directory structure** — chosen by the human author based on prior project conventions
- **CLI argument names** — manually picked for ergonomics
- **The decision to use a bounded pipeline vs. autonomous agents** — that was the human's call after Claude argued both sides; the human picked bounded for reproducibility
- **Dependency choices** — picked manually (e.g. `sentence-transformers` over OpenAI embeddings for offline-friendliness)

### Runtime AI vs. development AI

The system makes **5 Claude / Gemini calls per synthesis run**. That's the runtime AI use. The development AI use was vastly larger — Claude was the constant pair-programmer across every phase. The distinction matters because the rubric cares about both.

---

## 14. Sample Interview Questions (with prepared answers)

### Architecture

**Q1. Why a bounded five-agent pipeline instead of an autonomous agent loop?**
> Reproducibility, testability, and cost-bounded execution. Autonomous loops can take 0 or 50 LLM calls per task and we can't tell which case we're in until the bill arrives. A bounded pipeline always makes exactly 5 calls; the eval suite produces consistent results; the audit trail is linear and reviewable. We give up some adaptability — the system can't "decide" to call the Story Writer a second time after the Gap Detector finds problems — but that's a small loss for the gain.

**Q2. Why have a separate Parser agent? Couldn't the Story Writer read the transcript directly?**
> Three reasons. First, single-responsibility — the Parser's job is "what are people talking about" and the Story Writer's is "what should we build." Mixing them produces stories that aren't grounded in actual transcript content. Second, the topics list is reusable — the Story Writer reads it, the Gap Detector references topic IDs for evidence, the audit log uses topic IDs as the traceability anchor. Third, prompt simplicity — the Story Writer's prompt is 80 lines; merging it with parsing would put us at 200, where models start losing focus.

**Q3. The Gap Detector mixes local embeddings and an LLM call. Why?**
> Duplicate detection is a similarity problem — semantic distance between two text blobs. Embeddings do that natively at ~$0 per comparison. Conflict detection is a *reasoning* problem — "does this story violate constraint C-04?" — and that needs an LLM. So we split the work and use the right tool for each sub-task. As a bonus, embeddings-based dedup is deterministic, which makes the audit trail much easier to defend.

### Prompts

**Q4. Walk me through one prompt iteration cycle.**
> Sure. Iteration #5 was about the Story Writer suppressing stories on conflict-heavy meetings. We had a golden eval case (case_07) where every customer ask conflicts with PCI rules. The Story Writer was reading "this is blocked, this is blocked, this is blocked" and outputting zero stories — defensive but wrong, because then the Gap Detector has nothing to flag as a conflict either, and the audit trail loses the discussion entirely. We added an explicit rule: "Draft a story for every topic. Returning zero stories when topics is non-empty is a hard error." Plus a worked example. But the case *still* scored 0.33 — because the real culprit was one stage upstream: the Parser itself was filtering the "blocked" requests out before the Story Writer ever saw them, returning zero topics. Its "skip topics the team said no to" rule was misreading "PCI forbids that" as a decline. The fix was in the Parser — split "declined" (skip) from "blocked" (keep, so the conflict surfaces downstream). After that, case_07 went to 1.00 deterministic / 0.70 judge, verified directly. The lesson: when you split reasoning across agents, the *contracts between* them have to agree — a rule I added to fix the Story Writer silently contradicted a Parser rule, and the Parser ran first.

**Q5. How do you decide when a prompt rule should be added vs. when it should be left out?**
> We add a rule only after observing the failure mode in a real run — or a golden eval case. No speculative rules. Prompt sprawl is a real cost: models start ignoring later rules when the prompt gets too long. Every rule has to earn its place. We document iteration history in `docs/PROMPT_ENGINEERING.md` so the rationale survives.

### Evaluation

**Q6. Tell me about your evaluation strategy. How do you know the system works?**
> Three layers. Deterministic metrics — 6 of them, things like "story count in expected range" and "expected duplicates flagged" — give us a 0-1 score per case. We have 10 hand-curated golden cases including 3 negative ones (empty transcript, ambiguous customer panel, all-duplicates). LLM-as-judge gives us 5 qualitative scores (1-5, normalised) per case on dimensions the deterministic metrics can't catch — like "is the priority rationale data-driven." Most recent committed full run (`20260601T061247Z`, post-fix + V2 prompts): deterministic 0.88, judge 0.72 across 10 cases. Results are committed at `evaluation/results/<timestamp>/`. The judge score sits below the deterministic score mainly because it's harsh on correct-empty negative cases (case_05 scores 0.20 for correctly producing zero stories from a cancelled meeting); the deterministic score reflects whether the system actually produces correct output. A real grader can dig into either.

**Q7. What's a failure mode you found via evaluation that you'd never have caught by reading code?**
> The Epic Decomposer was silently dropping `priority_rationale`, `source_topic_id`, `evidence`, and `potential_constraint_conflicts` from every story. The schema example in the prompt didn't list those fields, so the model copied only what it saw. The Story Writer was producing them correctly; the next agent threw them away. The judge flagged "missing priority rationale" on multiple cases. Fixing it lifted case_02 from 0.67 to 0.83 deterministic. That bug would have survived any unit test — every test uses canned LLM responses with all the fields populated. Eval suites catch what tests can't.

### Production readiness

**Q8. What's missing before this could host real customer data?**
> Auth + per-user output isolation. Right now anyone who reaches the URL can read every saved run. Rate limiting + a USD cost ceiling per user — a loop at 10 runs per minute is $30 of unauthorised spend per hour. Forced PII redaction on untrusted uploads. Strict-redact halt-on-violation is already wired but defaults to off. The full P0/P1/P2 breakdown is in `PRODUCTION_READINESS.md`. I'd estimate 12-15 person-days to a defensible production state. None of it is architectural rework — the existing scaffolding extends cleanly.

**Q9. What would you do differently if you started this project today?**
> Two things. First, the `evidence` contract: the model used to be asked for fields A, B, C while the agent injected field D, and that split bit us when the Epic Decomposer dropped audit fields. The V2 prompts resolved it by making evidence *fully* system-owned — the model never produces it, so it can't be hallucinated or dropped — which I'd have done from day one. Second, I'd test the Parser on more "edge" transcripts earlier. The case_07 issue — Parser filtered out "blocked" topics — was a design oversight that survived because every transcript we tested with had at least one unblocked ask. (It's since fixed — §7 iteration 6 — but I'd rather have caught it before the eval did.)

### Operations

**Q10. How do you handle a Claude outage in production?**
> Today, badly — the run fails on the agent that hits the API, and downstream agents are marked skipped. The orchestrator returns a partial result with `parser` and maybe `constraint_extractor` populated, everything else empty. For production we'd want automatic provider failover: on a tenacity-exhausted `ToolError`, retry the same stage on Gemini. The orchestrator's `_build_tool_for_model` already supports per-stage provider switching; we just don't yet auto-switch on failure. That's a P1 in the readiness doc — about 1 day of work.

**Q11. Walk me through what happens if the user uploads a PDF transcript.**
> `input_loader.load_text` detects the `.pdf` suffix, lazy-imports `pypdf`, walks the pages, calls `extract_text()` per page, and concatenates with `--- Page N ---` separators between them. If the PDF is image-based and has no extractable text, we raise `InputError("No extractable text in <file>. The PDF may be image-based and require OCR")` — we don't OCR, since that's a different dependency and a different cost profile. From there it's text and the rest of the pipeline doesn't know or care about the original format.

**Q12. The audit log captures full prompts. What about cost and storage at scale?**
> Each event caps the prompt and response at 16 KB. A typical run has 5 tool-call events, so ~160 KB of audit data per run worst-case. At 10,000 runs that's 1.6 GB — easily handled by S3 or any object store. At 1 M runs (probably the wrong scale for this product anyway) we'd switch to selective capture: full prompts only on runs flagged for review, summaries-only on the rest. The audit log's data model is JSON-shaped, so this is a serialisation policy change, not a schema change.

---

## 15. What I'd Build Next

In rough priority order — the same list a reviewer would expect after asking "what's the roadmap?" The full P0/P1/P2 breakdown with effort estimates lives in [PRODUCTION_READINESS.md](../PRODUCTION_READINESS.md); this is the technical summary.

1. **Tool-use / structured-output for JSON.** Switch the agents from "ask for JSON, then `_extract_json_block`" to the provider's native structured-output mode (Anthropic tool-use, Gemini response schema). The fence-extraction code disappears and a malformed-JSON failure becomes impossible rather than caught-and-surfaced. Highest leverage, lowest risk.
2. **Provider failover on a stage failure.** Today a tenacity-exhausted `ToolError` fails the stage and skips downstream agents. `_build_tool_for_model` already supports per-stage provider switching — auto-retrying a failed Claude stage on Gemini (and vice-versa) is ~1 day of work and turns a Claude outage from "run fails" into "run degrades."
3. **~~Fix the Parser pre-filter on declined topics.~~ ✅ Done.** The case_07 eval surfaced that the Parser filtered out "blocked by a constraint" requests before the Story Writer saw them, so conflict-heavy meetings under-produced. Fixed in the Parser prompt by splitting "declined" from "blocked" (§7 iteration 6); case_07 went 0.33 → 1.00 deterministic, 0.00 → 0.70 judge. The remaining follow-up is a full-suite re-run to refresh the committed aggregate (Appendix C).
4. **~~Move `evidence` into the Story Writer prompt schema.~~ ✅ Resolved (the other way).** The split contract is gone — the V2 prompts removed `evidence` from the Story Writer's output entirely and made it fully system-owned (attached deterministically from `source_topic_id`). The Epic Decomposer's schema example was also corrected to show the real evidence shape so it can't be "normalized" away. See §7 "Prompts V2."
5. **Persist embeddings in a real vector DB.** `chromadb` is already in `requirements.txt` and the `MemoryStore` interface is shaped for the swap. Needed only once backlogs cross thousands of tickets or runs need to share an index across hosts.
6. **Write synthesized stories back to Jira.** The system drafts a backlog but stops at the human-review boundary. `scripts/seed_jira.py` already uses the same `POST /rest/api/3/issue` endpoint — turning approved stories into real issues is a clear next iteration, gated behind explicit user confirmation.
7. **Tamper-evident audit log.** The trail is append-only within a run but editable on disk afterward. Production should write events to an immutable store (S3 object-lock, or a hash-chained log).

None of these is architectural rework — the bounded-pipeline + injected-tools + shared-memory scaffolding extends cleanly into all of them.

---

## Appendix — Glossary

| Term | Meaning |
|---|---|
| **Agent** | A single-purpose Python class that wraps one LLM call (plus optional pre/post-processing). Five agents total in this system. |
| **Stage** | One step of the pipeline. One agent per stage. |
| **Memory** | The shared dictionary that agents read from and write to. Two layers: KV (structured) and vector (semantic). |
| **Audit trail** | Append-only log of every agent decision, persisted to `audit_trail.md` per run. |
| **Guardrails** | Post-LLM deterministic checks that catch common hallucination patterns. Non-blocking; surfaced to the user. |
| **Preset** | A named per-stage model configuration (Free / Balanced / Premium). |
| **Golden case** | A hand-curated input + expected-output pair in the evaluation suite. |
| **Run** | One complete invocation of the orchestrator. Produces one set of files in `outputs/<timestamp>/`. |
| **Topic** | A coherent ask, complaint, or observation extracted from the transcript. Topics are the bridge between unstructured input and structured stories. |
| **Constraint** | An architectural rule extracted from the wiki. Has severity (`must`, `should`, `forbidden`) and applies-to tags. |
| **Story** | A drafted user story with title, description, user_story (As a... I want... so that...), acceptance criteria, priority, rationale, tags, source-topic reference, and evidence. |
| **Conflict** | A flag indicating a new story contradicts a `must` or `forbidden` constraint. Surfaced by the Gap Detector. |
| **Duplicate** | A new story that overlaps significantly with an existing ticket. Detected by local embeddings. |
| **Gap** | A capability implied by the source material but absent from both the new stories and the existing backlog. |
| **Compare-mode** | Running the pipeline twice with different model presets and rendering a side-by-side delta. |
| **Live mode** | The tool fetches from a real REST API instead of a local fixture. Per-tool flag. |

---

## Appendix A — Elevator-Pitch Ladder

Scale the answer to the question. Three rehearsed lengths for "what is this?"

**30 seconds (the hook).**
> Backlog Synthesizer turns the messy inputs of a planning meeting — a transcript, an architecture wiki, and your existing Jira backlog — into structured, audited engineering work: epics, user stories with Given/When/Then acceptance criteria, and tasks. On top of that it flags three things a single prompt would miss: duplicates against work already in flight, conflicts with architectural constraints, and gaps the conversation implied but nobody wrote down. Five specialized agents, every LLM decision captured in a reviewable audit trail.

**2 minutes (the architecture).**
> It's a bounded five-agent pipeline — Parser, Constraint Extractor, Story Writer, Epic Decomposer, Gap Detector — each doing exactly one reasoning task and communicating through a shared memory store, never calling each other directly. An orchestrator sets the order, handles per-stage failure, and aggregates the result. The AI runs at exactly five points; everything else — sequencing, ID assignment, retrieval math, guardrails, output rendering — is deterministic Python, which is what makes it reproducible and testable. The Gap Detector is hybrid: it finds duplicates with local sentence-transformer embeddings (no LLM call) and only uses the LLM for the nuanced conflict-and-gap judgment. There are two providers (Claude and Gemini) selectable per stage via presets, a CLI and a Streamlit UI over the same engine, live Jira/Confluence integration, opt-in PII redaction, and a 10-case golden evaluation suite with deterministic metrics plus an LLM-as-judge.

**Deep dive (the "why").**
> Lead with the design tension: a single mega-prompt is what we built first (`versions/v1_baseline/`), and it degrades on every dimension at once because one prompt juggling five reasoning tasks loses focus — plus there's no intermediate state to audit and no way to recover from partial failure. Five agents is the smallest number that gives each one a single job. From there, walk whichever branch the interviewer pulls on: the retrieve-then-rerank RAG pattern, the prompt-iteration history, the audit/provenance chain (every story → topic ID → raw transcript quote), or the eval methodology. The throughline: treat the LLM as one more component that happens to be probabilistic — bounded usage, deterministic context, explicit fallbacks.

---

## Appendix B — Data Model / Schema Reference

Every artifact the pipeline produces, with the field, its type, the stage that produces it, and why it exists. This is the "walk me through your data model" answer in one place. Every artifact carries a single `id` field, and the orchestration layer guarantees those ids are sequential and unique — overwriting the model's value for topics and constraints, backstopping it for stories and epics. (The V2 prompts deliberately use one `id` field everywhere rather than per-type names like `topic_id`/`epic_id`, which previously produced a confusing dual id on epics.)

### `topic` — produced by the Parser

| Field | Type | Why it exists |
|---|---|---|
| `id` | str `T-NN` | Stable anchor; the traceability key everything downstream references |
| `theme` | str (kebab-case) | Short machine-friendly label for the ask |
| `summary` | str | 1-2 sentence description of the topic |
| `raw_quote` | str | **Verbatim** transcript lift — the root of the audit chain |
| `speaker` | str | Who said it (or `""`) |
| `sentiment` | str | `concern` / `request` / `observation` etc. — triage signal |

### `constraint` — produced by the Constraint Extractor

| Field | Type | Why it exists |
|---|---|---|
| `id` | str `C-NN` | Referenced by story `potential_constraint_conflicts` and by `conflict.with` |
| `severity` | enum `must` / `should` / `forbidden` | Drives whether a violation is a hard conflict |
| `category` | str | `compliance` / `performance` / `integration` / `offline` / `platform` … |
| `statement` | str | The rule, normalized into a full sentence |
| `source_excerpt` | str | Verbatim wiki text the rule was derived from (provenance) |
| `applies_to` | list[str] | Surface tags (`pos`, `mobile-app`, `pharmacy` …) for relevance filtering |

### `story` — produced by the Story Writer (then enriched)

| Field | Type | Producer | Why it exists |
|---|---|---|---|
| `id` | str `ST-NN` | Story Writer (Python) | Stable story key |
| `title` | str | LLM | Short imperative title |
| `description` | str | LLM | Context + the constraints it threads through |
| `user_story` | str | LLM | "As a … I want … so that …" |
| `acceptance_criteria` | list[str] | LLM | Given/When/Then, 2-5 expected (guardrail-checked) |
| `priority` | enum `High`/`Medium`/`Low` | LLM | Triage |
| `priority_rationale` | str | LLM | Concrete justification (guardrail-checked for High) |
| `tags` | list[str] | LLM | From canonical vocabulary (guardrail-checked) |
| `source_topic_id` | str `T-NN` | LLM | Links story → topic (grounding) |
| `potential_constraint_conflicts` | list | LLM | Story Writer's *self-flag*; distinct from Gap Detector's verdict |
| `evidence` | list[obj] | **Story Writer (Python post-proc)** | `{topic_id, theme, raw_quote, speaker, sentiment}` — injected from the cited topic, not generated. The V2 prompt no longer asks the model for it, so it can't be hallucinated. |
| `tasks` | list[`task`] | **Epic Decomposer** | Added later; absent until Stage 4 |

### `task` — produced by the Epic Decomposer

| Field | Type | Why it exists |
|---|---|---|
| `id` | str `ST-NN-TK-NN` | Hierarchical id ties the task to its parent story |
| `title` | str | One concrete, pick-up-able unit of work |
| `type` | str | `backend` / `frontend` / `data` / `infra` … |

### `epic` — produced by the Epic Decomposer

| Field | Type | Why it exists |
|---|---|---|
| `id` | str `EP-NN` | Epic key |
| `title` | str | Theme name (a real theme, not a bucket — see §7 iteration 4) |
| `description` | str | What the epic groups and why |
| `stories` | list[`story`] | The same story dicts, now nested and carrying `tasks` |

### `duplicate` — produced by the Gap Detector (embeddings by default)

| Field | Type | Why it exists |
|---|---|---|
| `story_id` | str `ST-NN` | Which new story is the duplicate |
| `existing_id` | str | Backlog id it overlaps (`#1156`, or live `NS-47`) |
| `confidence` | enum `high`/`medium`/`low` | Reviewer triage |
| `reason` | str | One-sentence justification |
| `_similarity` | float (optional) | Cosine score, present only on the embeddings path |

### `conflict` — produced by the Gap Detector (LLM)

| Field | Type | Why it exists |
|---|---|---|
| `story_id` | str `ST-NN` | Which story conflicts |
| `with` | str `C-NN` | Which constraint it violates |
| `severity` | str | How serious the contradiction is |
| `reason` | str | Why it's a conflict, not just topic adjacency |

### `gap` — produced by the Gap Detector (LLM)

| Field | Type | Why it exists |
|---|---|---|
| `id` | str `G-NN` | Stable gap id |
| `title` | str | The missing capability |
| `description` | str | Why it's implied but unwritten |
| `related_ids` | list[str] | The new-story / existing-ticket ids this gap relates to (the Gap Detector sees stories + tickets, not topics) |
| `evidence` | str | One grounded sentence pointing to why the gap exists (kept a string so it renders cleanly in `synthesis.md` and the UI) |

### `guardrail_finding` — produced by `guardrails.py` (deterministic)

| Field | Type | Why it exists |
|---|---|---|
| `code` | str | Machine code (see Appendix F) |
| `severity` | enum `error`/`warn`/`info` | Drives UI styling and reviewer attention |
| `message` | str | Human-readable explanation |
| `story_id` | str \| null | Which story triggered it (null for run-level) |

### Top-level `result` dict (what the orchestrator returns)

`summary`, `topics[]`, `constraints[]`, `epics[]` (stories nested inside), `gaps[]`, `conflicts[]`, `duplicates[]`, `guardrail_findings[]`, `audit_trail` (markdown), `token_usage` (`{agent: {input, output}, total}`), `model` (display string), `models` (per-stage dict). In compare-mode the shape is instead `{compare_mode, primary, secondary, labels, comparison}`.

---

## Appendix C — Defending the Evaluation Numbers

This is the question most likely to put you on the back foot: *"Your LLM-judge score is below your deterministic score — isn't that bad?"* Have this ready. The numbers below are the committed post-fix run `evaluation/results/20260601T061247Z/` (10 cases, V2 prompts). For reference, the pre-fix baseline `20260528T154409Z/` scored 0.80 / 0.53 — so the case_07 Parser fix (§7 iteration 6) moved the aggregate to 0.88 / 0.72.

| Case | Theme | Deterministic | LLM-judge | Read |
|---|---|---:|---:|---|
| 01 | Q3 planning (happy path) | 0.83 | 0.80 | Solid |
| 02 | Pharmacy escalation | 0.83 | 0.65 | Solid |
| 03 | Mobile Slack standup | 0.83 | 0.70 | Solid |
| 04 | Support note (negative) | 0.83 | **0.85** | Hallucination-resistant ✓ |
| 05 | Empty / cancelled meeting | 0.83 | **0.20** | Correct-empty, judge penalizes anyway |
| 06 | All-duplicates | 0.83 | 0.80 | Solid |
| 07 | Conflict-heavy | **1.00** | **0.90** | Was the real bug — now fixed (§7 iter 6) |
| 08 | Ambiguous customer panel | **1.00** | **0.90** | Conservative extraction ✓ |
| 09 | Multi-team planning | 0.83 | 0.65 | Solid |
| 10 | Compliance (HIPAA/GDPR/PCI) | **1.00** | 0.75 | Regulatory drivers surfaced ✓ |
| | **Committed average** | **0.88** | **0.72** | `20260601T061247Z` (post-fix, V2 prompts) |

**The honest, confident answer:**

> The deterministic average is 0.88 and the judge is 0.72 — and I can account for the gap precisely. The single biggest judge drag is **case 05** (empty / cancelled meeting): it scores 0.20 on the judge but 0.83 deterministic, because producing zero stories from a cancelled meeting is the *correct* behaviour and the LLM-judge instinctively penalizes empty output — a known failure mode of LLM-as-judge on negative cases. So the largest remaining judge "miss" is the judge being harsh on a correct answer. Every other case lands 0.65–0.90 on the judge. The negative/edge cases that prove the system doesn't hallucinate (04, 08) score at the top. I report both numbers — deterministic for objective correctness, judge for qualitative depth — and being able to explain exactly where they diverge is more honest than reporting one flattering figure.

> For context on *why* I trust this: the earlier committed run (`20260528T154409Z`, 0.80 / 0.53) was pre-fix. case_07 was scoring 0.33 / 0.00 because the **Parser** — not the Story Writer — was filtering out requests blocked by a constraint (it read "PCI forbids that" as the team declining), so no stories were drafted and no conflict could be flagged. I found it *through* the eval. The fix (§7 iteration 6) splits "declined" from "blocked," and the committed re-run confirms case_07 now scores 1.00 / 0.90 — the full suite, not a projection.

**Why keep two scoring systems at all?** Deterministic metrics are objective but shallow (they check counts, keyword presence, expected duplicates/conflicts). The LLM-judge is subjective but deep (it can assess "is this acceptance criterion genuinely testable?"). Reporting both — and being able to explain where they diverge — is more honest than reporting one flattering number. The six deterministic metrics are in `evaluation/metrics.py`: `story_count_in_range`, `acceptance_criteria_well_formed`, `required_topics_present`, `forbidden_topics_absent`, `expected_duplicates_found`, `expected_constraint_conflicts_found`.

---

## Appendix D — Single-prompt vs. 5-agent (the honest, measured comparison)

An interviewer will push on the core claim: *"How do you **know** five agents beat one prompt?"* Here is the honest answer, with real numbers — including where the answer is "we don't, on quality alone."

**Terminology first:** `versions/v1_baseline/` is **not** a single-prompt system — it's an earlier *multi-agent* snapshot (same five agents, fewer features). The genuine single-mega-prompt comparison is `evaluation/single_prompt_baseline.py`: one LLM call, given the same role and rules, that emits the entire synthesis (topics + epics + stories + duplicates + conflicts + gaps) in one shot. It's scored with the *same* metrics + judge on the *same* 10 golden cases.

**The measured result** (committed: `evaluation/results/single_prompt_20260601T081045Z/` vs `20260601T061247Z`):

| | Single mega-prompt | 5-agent pipeline |
|---|---:|---:|
| Deterministic avg | 0.84 | **0.88** |
| LLM-judge avg | **0.86** | 0.72 |

**The honest read — don't overclaim.** On these small/medium cases, output quality is **roughly comparable**. The single prompt even edges out on the LLM-judge (which is noisy — see Appendix C). The pipeline's deterministic advantage is real but **concentrated in duplicate detection**: `case_06` (all-duplicates) scored **0.83 vs 0.33**, because the pipeline offloads dedup to local embeddings while a single prompt must hold the whole backlog in context and reason over it inline. The single prompt actually won `case_03` and `case_04`; the rest tie.

**So why five agents?** Not a single-input quality knockout — the justification is **structural**, and it's what the eval *can't* capture on small inputs:
- **Auditability** — intermediate `topics`/`constraints` artifacts with IDs and a per-agent trail; a single prompt is one blob in, one blob out.
- **Partial-failure recovery** — re-run only the failed stage, not the whole synthesis.
- **Per-stage cost control** — spend Sonnet only where it matters (the Balanced preset).
- **Duplicate detection + scaling** — the measured `case_06` win, which *widens* as the backlog grows past what fits in one context window (retrieve-then-rerank still works; a single prompt can't fit hundreds of tickets).

This is the mature version of the argument: *"Quality is comparable on small inputs — I measured it. We chose the multi-agent design for auditability, recoverability, cost control, and scaling, plus a measured correctness edge on duplicate detection — not for a quality number I can't back."* A grader who runs `python evaluation/single_prompt_baseline.py` gets the same answer.

---

## Appendix E — Memory Store Internals

`src/memory/store.py` is how agents hand off state without calling each other. Two layers, one class.

**KV layer** — a plain dict behind `get(key, default)` / `put(key, value)` / `append(key, value)`. Keys are the artifact names: `topics`, `constraints`, `stories`, `epics`, `gaps`, `conflicts`, `duplicates`, `existing_tickets`, `summary`. This *is* the agent-handoff bus: the Story Writer does `memory.get("topics")`, the orchestrator seeds `memory.put("existing_tickets", …)`. No agent ever holds a reference to another agent.

**Vector layer** — `index_tickets(tickets)` and `search_similar(query, top_k=5)`. Mechanics:
- Below `_RETRIEVAL_THRESHOLD = 20` tickets, `index_tickets` returns `False` and `search_similar` returns the full list — small backlogs skip embeddings entirely.
- At/above 20, it lazy-imports `sentence_transformers`, loads `all-MiniLM-L6-v2`, and encodes every ticket's `title + ". " + description` with `normalize_embeddings=True` (so a dot product *is* cosine similarity).
- `search_similar` encodes the query, computes `sims = query_vec @ ticket_vectors.T`, takes `argsort(-sims)[:top_k]`, and returns those tickets each tagged with a `_similarity` float.
- If `sentence_transformers` isn't installed, it logs a warning and falls back to no-embedding mode — the pipeline still runs.

**The content-addressed cache** (`MEMORY_PERSISTENT=1` or `persistent=True`):
- Vectors are saved to `.cache/memory/vectors/<corpus_hash>.npz`. The `corpus_hash` is a SHA-256 of the **model name + every ticket's text** (first 16 hex chars). Because the hash includes the corpus, it **self-invalidates**: change any ticket and the cache key changes, so you never read stale vectors. Re-indexing the same backlog becomes a disk read instead of a fresh encode (~5-10s saved on 50 tickets).
- KV state is also mirrored to `.cache/memory/kv/<key>.json` on each `put()`, and `hydrate_from_disk()` can reload a prior run's memory for inspection/replay without re-running the pipeline.

**Interview soundbite:** "Agents share a dict, not references — that's what keeps the dependency graph a clean DAG the orchestrator owns. The vector layer is the same store with a numpy index bolted on, and the cache is content-addressed so it can never serve stale embeddings."

---

## Appendix F — The Full Guardrail Catalog

`src/guardrails.py` runs six checks after synthesis, emitting findings at three severities. Findings are **non-blocking** — they annotate the result and are audit-logged; they never fail the run. This is the "how do you catch hallucination deterministically?" answer: the two `error`-level codes are exactly the un-traceable-story signals.

| Code | Severity | Fires when | Check |
|---|---|---|---|
| `ac_count_too_low` | warn | A story has < 2 acceptance criteria | 1 (AC count) |
| `ac_count_too_high` | info | A story has > 7 acceptance criteria | 1 (AC count) |
| `ac_missing_gwt` | warn | An AC lacks Given/When/Then keywords | 2 (AC grammar) |
| `duplicate_title` | warn | Two stories share a normalized title | 3 (unique titles) |
| `non_canonical_tag` | info | A tag is outside the 15-word canonical vocabulary | 4 (canonical tags) |
| `ungrounded_story` | **error** | Story has no `source_topic_id` | 5 (grounding) |
| `dangling_topic_ref` | **error** | `source_topic_id` points to no real topic | 5 (grounding) |
| `missing_evidence` | info | Story's evidence block is empty | 5 (grounding) |
| `weak_priority_rationale` | warn | A High-priority story's rationale is < 20 chars | 6 (rationale rigor) |

The canonical tag set (15 tags): `pos, mobile-app, ecommerce, loyalty, inventory, pharmacy, vendor-portal, store-associate, analytics, payments, offline-mode, accessibility, performance, security, compliance`. New tags are *allowed* (the prompt permits inventing one when nothing fits) but flagged `info` so a human decides whether the vocabulary should grow. Why heuristics and not a second LLM call? Because these checks must be cheap, mandatory, and deterministic — the LLM-as-judge in `evaluation/` is the deep qualitative pass; guardrails are the fast guaranteed one.

---

## Appendix G — Determinism & Concurrency

*"Is your pipeline deterministic?"* — a precise answer signals seniority.

**Deterministic given the same inputs and models:** the orchestration order, stage skipping, retry behavior, deterministic ID assignment (`T-`/`C-`/`ST-`/`EP-`/`-TK-`), the embedding vectors (same model + same text → same vector), cosine similarity and top-K selection, all six guardrail checks, PII redaction (regex), and output formatting.

**Not deterministic:** the LLM outputs themselves — but the variance is *bounded* by the strict JSON schema in each prompt. Two runs may word a story differently; they won't change the result *shape*, the field set, or the traceability links. This is exactly why all 128 tests mock the LLM: the deterministic layers are identical in test and production, so testing them with canned responses is faithful.

**Concurrency:** the orchestrator is single-threaded per run. Stages run sequentially even where they're logically independent (Parser and Constraint Extractor depend on different inputs and *could* run in parallel — see Appendix I). Compare-mode runs the two provider legs **sequentially, not concurrently**, partly to stay within Anthropic's per-key rate limit and partly because the `AuditLog` isn't thread-safe. Parallelizing independent stages is a deliberate future optimization, not an accident.

---

## Appendix H — Security & Privacy Posture

Consolidated answer to *"would you let a customer upload real data?"* (Today: not without the P0 items below.)

**What PII redaction does** (`src/redactor.py`, opt-in via `--redact-pii` / UI toggle):
- Regex patterns for `EMAIL`, `PHONE` (intl / US / IN forms), `SSN` (dashed form only), `CARD` (13-16 digit), and two-word capitalized personal `NAME`s.
- **Stable placeholders**: the same value maps to the same token across all three inputs (`[NAME_3]` everywhere), which is what lets the Gap Detector still match a redacted name in a story to the same redacted name in an existing ticket.
- **Conservative by design**: names use an explicit blocklist (~80 words like "Customer", "Meeting", "Action", weekdays, vendor names) to avoid tokenizing headings as people. The documented trade-off is *low false-positive, accepts some false-negatives* — the goal is reducing casual PII in LLM logs, not airtight de-identification.
- On the way out, the synthesis is **un-redacted** so the user sees real names; the **audit log stays redacted** on purpose — it's the artifact a compliance reviewer inspects.

**The trust boundary** (`strict_redact`): after redaction, every LLM-bound input is re-scanned; if any pattern slips through, the run **halts** with `StrictRedactionViolation` and an audit event that records the *kind* and *context* of the leak but **not the raw PII**. Wired and tested, but defaults to off.

**What's still open (production gaps, from `PRODUCTION_READINESS.md`):**
- **No auth / no per-user output isolation** — anyone reaching the URL can read every saved run under `outputs/` and `logs/runs/`.
- **No rate limit / cost ceiling** — a loop at 10 runs/min is unauthorized spend.
- **Redaction defaults off** and is regex-grade, not ML-grade — fine for casual PII, not a compliance guarantee.
- **Audit log is append-only within a run but editable on disk** afterward — not tamper-evident.

Estimated 12-15 person-days to a defensible production state; none of it is architectural rework.

---

## Appendix I — Latency Breakdown

*"Why ~50 seconds, and how would you speed it up?"* Wall time on the bundled sample is ~30-60s on Sonnet, ~15-25s on Gemini Flash. Where it goes:

| Cost | Magnitude | Notes |
|---|---|---|
| 5 sequential LLM calls | dominates (~80-90%) | Each is a network round-trip + generation; Story Writer (8000-token budget) is the slowest |
| Embedding the backlog | ~5-10s first run (50 tickets) | One-time model load + encode; **cached** after with `MEMORY_PERSISTENT=1` |
| Local cosine similarity | milliseconds | One numpy matmul |
| Guardrails + formatting | milliseconds | Pure Python |

**How to speed it up, in order of payoff:**
1. **Parallelize Stage 1 + Stage 2** — the Parser (transcript) and Constraint Extractor (wiki) have no data dependency on each other. Running them concurrently removes one full LLM round-trip from the critical path. (Currently sequential for a simpler audit trail — see Appendix G.)
2. **Use the cheaper model where quality allows** — the Balanced preset already does this; Free (all Gemini Flash) roughly halves wall time.
3. **Persist embeddings** (`MEMORY_PERSISTENT=1`) — removes the encode cost on repeat runs against the same backlog.
4. **Stream the UI, not the pipeline** — the progress callback already lights stages up live, so perceived latency is lower than wall time.

Note the pipeline is interactive (≤60s), which is why there's deliberately **no queue/worker** (Celery/RQ) — that's overkill for v1 (see §2.4).

---

## Appendix J — Code Map

One line per file — the "where would I find X?" cheat sheet.

**Entry points**
- `src/main.py` — CLI: arg parsing, `.env` load, `JIRA_*`→`CONFLUENCE_*` promotion, output writing
- `app.py` — Streamlit UI (~1,800 lines): sidebar inputs, live pipeline, KPI/story cards, compare-mode, run history

**Orchestration & agents**
- `src/orchestrator.py` — the pipeline: stage sequencing, per-stage model dispatch, live fetch, redaction, token aggregation, compare-mode, dry-run
- `src/agents/base.py` — `Agent` base class + `AgentError`
- `src/agents/parser_agent.py` — Stage 1 (transcript → topics)
- `src/agents/constraint_agent.py` — Stage 2 (wiki → constraints)
- `src/agents/story_writer_agent.py` — Stage 3 (topics + constraints → stories; injects evidence)
- `src/agents/epic_decomposer_agent.py` — Stage 4 (stories → epics + tasks)
- `src/agents/gap_detector_agent.py` — Stage 5 (embeddings dupes + LLM conflicts/gaps)

**Tools (provider & integration wrappers)**
- `src/tools/base.py` — `Tool` interface, `ToolError`, `VisionAttachment`
- `src/tools/claude_tool.py` — Anthropic wrapper: retry, JSON extraction, vision payload
- `src/tools/gemini_tool.py` — Gemini wrapper (new `google-genai` SDK), same interface
- `src/tools/embedding_tool.py` — sentence-transformers duplicate detection
- `src/tools/jira_tool.py` / `confluence_tool.py` / `github_tool.py` — REST integrations (mock + live)

**Memory, guardrails, support**
- `src/memory/store.py` — KV + vector store, content-addressed cache (Appendix E)
- `src/memory/audit_log.py` — append-only audit trail, markdown renderer
- `src/guardrails.py` — six post-synthesis checks (Appendix F)
- `src/redactor.py` — PII redaction + strict-redact boundary (Appendix H)
- `src/pricing.py` — per-model token→USD rates
- `src/input_loader.py` — txt/md/pdf + ticket JSON loading
- `src/output_formatter.py` — JSON + Markdown rendering
- `src/logger_setup.py` — logging config
- `src/ui/styling.py` — the ~1,000-line dark-dashboard CSS

**Prompts** (`prompts/`) — `system_prompt.md` + one per agent
**Evaluation** (`evaluation/`) — `run_evaluation.py`, `metrics.py`, `llm_as_judge.py`, `ab_compare.py`, `dashboard.py`, `single_prompt_baseline.py`, `golden_dataset/` (10 cases)
**Tests** (`tests/`) — 9 files, 128 tests, all mocked
**Baseline** — `versions/v1_baseline/` — an earlier multi-agent snapshot; the genuine single-prompt comparison is `evaluation/single_prompt_baseline.py` (Appendix D)

---

## Appendix K — Scoping & Approach (why this, why not the others)

### How we scoped the problem

The brief: turn unstructured engineering inputs (meeting transcripts, an architecture wiki, an existing backlog) into a **structured, audited** set of backlog items, with duplicate / conflict / gap detection. We scoped it as:

**In scope (and built):** ingest txt/md/pdf transcripts + a wiki + JIRA/GitHub tickets; produce epics → stories (Given/When/Then AC, priority + rationale, tags) → tasks; detect duplicates (vs. the backlog), conflicts (vs. architectural constraints), and gaps; a full audit trail; CLI **and** Streamlit UI; mock **and** live Atlassian; Jira write-back; an evaluation harness.

**Deliberately out of scope:** two-way Jira sync, auth / multi-tenant isolation, autonomous re-planning loops, OCR for scanned PDFs, and model fine-tuning. Each is noted in `PRODUCTION_READINESS.md` rather than hidden.

**Hard constraints we held to:** reproducibility, testability with zero API spend, cost-bounded execution (a fixed number of LLM calls), and auditability (the brief explicitly requires "audit logs must show how conclusions were reached").

### Why bounded multi-agent — and why not the alternatives

| Approach considered | Why rejected (or chosen) |
|---|---|
| **Single mega-prompt** | We *built and measured* it (Appendix D): comparable quality on small inputs, but no intermediate artifacts to audit, no partial-failure recovery, and duplicate-detection collapses once the backlog won't fit a context window. |
| **Autonomous agent loop** (ReAct / "let the model decide what to call next") | Unbounded cost and call count, hard to budget, hard to test, and the audit trail becomes a graph instead of a linear trace. We wanted bounded + reproducible. |
| **Two agents (Extract + Synthesize)** | The synthesizer ends up juggling constraints + backlog matching + prioritization + decomposition in one prompt — the same focus problem as one mega-prompt. |
| **No-code / SaaS** (Zapier, n8n + one LLM call) | Can't do the constraint-vs-story conflict reasoning or produce a defensible audit chain; and it wouldn't demonstrate engineering. |
| **Fine-tuning a custom model** | Data-hungry, slow to iterate, and opaque. Prompt engineering + RAG reached the goal faster and stays explainable. |
| **Bounded multi-agent (chosen)** | One reasoning task per agent, shared audited memory, deterministic ordering → reproducible, testable, cost-bounded, auditable. |

**Honest self-assessment of the scoping:** we matched scope to a demo timeline — broad enough to show agentic orchestration + RAG + audit + live integrations + write-back, bounded enough to stay testable and honest. The committed single-prompt baseline (Appendix D) is the evidence that we *measured* the core architecture decision rather than just asserting it.

---

## Appendix L — Why these LLMs (and not others)

The provider is chosen **per stage** (Free / Balanced / Premium presets + per-stage override), so this is a cost-vs-quality dial, not a single bet.

**Claude Sonnet 4.5** — primary for the hardest reasoning (the Story Writer) and all of Premium. Why: the most reliable instruction-following + JSON-only output in our testing, strong long-context handling, and it's vision-capable (whiteboard photos). The nuanced "same topic vs. same work" dedup and the priority rubric behaved most consistently here.

**Gemini 2.5 Flash** — the Free preset and the four mechanical stages in Balanced. Why: fast and cheap for the more mechanical extraction/decomposition work, with a generous free tier so a reviewer without a Claude key can still run it.

**Why not the others:**
- **GPT-4o / OpenAI** — would work; we standardized on Anthropic for the strongest structured-JSON instruction-following in our testing and to avoid a third SDK + key. The architecture is provider-agnostic, so it's a small add (Appendix M).
- **Claude Opus** — best reasoning, but ~5× Sonnet's cost; this is structured extraction, not deep open-ended reasoning, so Opus is overkill.
- **Claude Haiku** — cheap and fast, but the quality drop on the priority rubric and dedup-vs-same-topic wasn't worth it on the *reasoning* stages. It's a perfectly good per-stage choice for the Parser/Constraint Extractor if cost matters — and the per-stage picker makes that a one-click change.
- **Local / open models (Llama, etc.)** — no key, private, but weaker JSON reliability and real ops burden. We *did* go local exactly where local is the right call: the **embedding model** (`all-MiniLM-L6-v2`) for duplicate detection — free at runtime, offline, ~80 MB, and plenty accurate for "find similar tickets."

---

## Appendix M — Adding a new model / provider (exact changes)

The tool abstraction means a new provider is a contained change. To add, e.g., OpenAI GPT-4o:

1. **New tool** — `src/tools/openai_tool.py`, mirroring `ClaudeTool`: implement `call_for_json(user_message, max_tokens, images=…) -> (dict, usage)`, reuse the same JSON-extraction + `tenacity` retry pattern, lazy-import the SDK, and raise `ToolError` on a missing key / API error.
2. **Dispatch** — `src/orchestrator.py::_build_tool_for_model`: add `elif mid.startswith("gpt"): return OpenAITool(model=model_id)`.
3. **Failover partner** — `src/orchestrator.py::_fallback_model`: decide what it fails over to (e.g. `gpt-* → claude-sonnet-4-5`).
4. **Pricing** — `src/pricing.py`: add the model's input/output $/1M rates so the cost panel stays accurate.
5. **Presets / picker** — `app.py`: add it to `MODEL_PRESETS` and the per-stage `MODEL_OPTIONS` selectbox; optionally `DEFAULT_STAGE_MODELS` in the orchestrator.
6. **Vision** — if it's vision-capable, give its `call_for_json` an `images=` parameter (the Parser forwards images only to tools whose signature includes `images`). If not, the existing vision auto-switch already routes images to Claude.
7. **Env** — read the key in the tool's `__init__`; add it to `.env.example`.
8. **Tests** — add a tool-level test mocking the SDK. The agents and orchestrator are provider-agnostic (they inject fakes), so they need no changes.

**What you do NOT touch:** the five agents, memory, audit log, guardrails, redactor, output formatter. They only ever see `call_for_json` — that's the payoff of the abstraction, and a strong thing to point to in a code review.

---

## Appendix N — Code-review walkthrough (interview prep)

A module-by-module guide for "they'll review the code." For each: **what it does**, the **design decisions** worth defending, and **likely questions**. Cross-cutting themes to lead with: *the LLM does judgment, deterministic Python does everything else; tools are injectable so the suite is fully mocked; errors degrade gracefully; every decision is audited.*

### `src/main.py` — CLI entry
Loads `.env`, promotes `JIRA_*` → `CONFLUENCE_*` (one Atlassian token), parses args, loads inputs via `input_loader`, runs `Orchestrator().run(...)`, writes `synthesis.json/.md` + `audit_trail.md`, and (with `--publish-jira`) publishes to Jira. Exit codes: 0 ok, 2 input error, 3 orchestrator error.
*Likely Q:* "How does a PDF transcript get handled?" → `load_text` lazy-imports `pypdf`, joins pages, raises `InputError` with an OCR hint on image-only PDFs.

### `src/orchestrator.py` — the coordinator (read this one closely)
Constructs a fresh `MemoryStore` + `AuditLog` per run (no cross-run state), resolves **per-stage models**, applies the **vision auto-switch** and optional **PII redaction** + live fetches, then runs the five stages in fixed order. Each stage: `_emit("started")` → build the tool (`_tool_for`) → `agent.run()` → `_emit("completed")`; on `AgentError` it calls `_attempt_failover` (retry on the other provider, gated by `auto_switch`). Ends by aggregating token usage, assembling the result dict, un-redacting the output, and running guardrails. Key helpers: `_build_tool_for_model` (prefix dispatch), `_fallback_model`, `_attempt_failover`, `run_compare` (two providers, sequential).
*Likely Q:* "Why is it a fixed pipeline, not autonomous?" (reproducibility/cost/audit) · "What happens if stage 3 fails?" (recorded, downstream skipped, partial result returned — or failover if enabled).

### `src/agents/base.py` + the five agents
`Agent` base: `name`, `memory`, `audit`, `load_prompt()`, `emit()`. Every agent follows the same shape: **substitute the prompt → `call_for_json` → assign deterministic IDs → write to memory → `record_tool_call`**. Specifics: the **Parser** forwards images when the tool supports them; the **Story Writer** post-attaches the `evidence` block from the cited topic (so it can't be hallucinated); the **Gap Detector** is hybrid — embeddings for duplicates (no LLM), LLM for conflicts + gaps, with a `G-NN` id backstop.
*Likely Q:* "Why assign IDs in Python, not let the model?" (determinism, no hallucinated/duplicate ids) · "Why a separate Parser?" (single responsibility + reusable topic anchors for traceability).

### `src/tools/` — provider + integration wrappers
`base.py`: `Tool`, `ToolError`, `VisionAttachment` (`from_path`/`from_bytes`). `claude_tool.py`: Anthropic client, shared `system` prompt, `_call_with_retry` (tenacity, retries only transient errors), multimodal content array, and `_extract_json_block` (tries a fenced block, then the first `{…}` span). `gemini_tool.py`: same `call_for_json` contract over `google-genai`. `embedding_tool.py`: lazy `all-MiniLM-L6-v2`, `find_duplicates` via normalized-vector cosine ≥ 0.6 (no LLM). `jira_tool.py`: mock (fixture) vs live (paginated JQL + ADF→text) **and write-back** (`create_issue` with progressive fallback, `publish_synthesis` building Epic→Story→Sub-task).
*Likely Q:* "Why the regex JSON extraction instead of tool-use?" (small, well-tested; tool-use is the documented next step) · "Why local embeddings?" (free, offline, deterministic; dedup is similarity, not reasoning).

### `src/memory/` — store + audit
`store.py`: KV (`get`/`put`) for agent handoff + a vector layer (`index_tickets` only embeds at ≥ 20 tickets; `search_similar` returns top-K with `_similarity`); optional content-addressed `.npz` cache keyed by a SHA-256 of model + corpus (self-invalidating). `audit_log.py`: append-only `AuditEvent`s (`record`, `record_tool_call`, `record_failure`), rendered to Markdown with collapsible `<details>` prompt/response blocks.
*Likely Q:* "How do agents share state?" (a dict + a numpy index — never references to each other; the orchestrator owns sequencing).

### `src/guardrails.py`, `src/redactor.py`, `src/pricing.py`
Guardrails: six deterministic post-synthesis checks (AC count/grammar, unique titles, canonical tags, story grounding, priority-rationale rigor) → non-blocking findings, the two `error`-level ones are the deterministic hallucination catch. Redactor: regex PII (email/phone/SSN/card + conservative names) with stable placeholders + a strict-redact trust boundary. Pricing: per-model $/1M rates for the cost panel.
*Likely Q:* "How do you catch hallucination deterministically?" (the `ungrounded_story` / `dangling_topic_ref` guardrails).

### `src/input_loader.py`, `src/output_formatter.py`
Loader: txt/md (UTF-8 + latin-1 fallback), pdf (pypdf), json tickets (list or `{items:[…]}`), normalized shape. Formatter: renders the result to `synthesis.json` + a hierarchical `synthesis.md` (epics → stories → tasks, then gaps/conflicts/duplicates).

### `app.py` — Streamlit UI
State in `st.session_state` (Streamlit re-runs the whole script per interaction). Sidebar = multi-select sources + always-visible uploaders + presets; the run handler defines a `progress_callback` that **accumulates** the live log and lights the pipeline; results render as KPI cards + tabs + downloads + Create-in-Jira; the top nav and dialogs reuse the same engine. The UI is a thin presentation layer — it calls the same `Orchestrator.run` the CLI does.
*Likely Q:* "Why Streamlit?" (Python-native, fast, server-rendered — no separate frontend) · "Is the UI tested?" (the engine is; UI is a presentation wrapper over the tested orchestrator).

### `evaluation/`
`metrics.py` (6 deterministic 0–1 scores), `llm_as_judge.py` (5 qualitative dims, normalized), `run_evaluation.py` (per-case runner, mockable orchestrator), `single_prompt_baseline.py` (the honest A/B), `dashboard.py` (regression detection), `ab_compare.py` (prompt experiments).
*Likely Q:* "Why both deterministic and LLM-judge?" (objective-but-shallow vs. subjective-but-deep; reporting both + explaining where they diverge is more honest — Appendix C).

**Code-review tips:** lead with the deterministic-vs-AI boundary; when asked "where would X change?", point at the single owning module (e.g., a new provider → `_build_tool_for_model` + a new tool, Appendix M); and be ready to name one thing you'd improve (tool-use for guaranteed JSON; two-way Jira sync; parallelizing the two independent stages).
