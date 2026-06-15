# AI-First SDLC — Assessment Answers

> **Grounded in the Backlog Synthesizer project:** a multi-agent system that ingests customer meeting transcripts, architecture wikis, and existing JIRA/GitHub backlogs, then synthesizes them into a traceable hierarchy of epics → stories → tasks, while detecting gaps, conflicts, and duplicates. Five specialized agents on a LangGraph StateGraph (7 nodes total), multi-provider LLM layer (Claude / Gemini / Ollama) behind per-provider circuit breakers, vector memory, tamper-evident audit log, deterministic + LLM-judge evaluation harness, and containerized deployment.

---

## General Understanding

### 1. What does "AI-First SDLC" mean to you? How is it different from traditional development with AI tools added in? Give an example of how you've applied AI-First thinking in a project you've worked on.

**AI-First** means the system is *architected around* probabilistic reasoning as a first-class component — with the same engineering rigor we'd apply to any unreliable dependency: bounded inputs, validated outputs, observability, fallbacks, and a test harness. "AI tools added in" means a deterministic app that occasionally calls an LLM as a convenience; the AI is a feature. In AI-First, the AI is a *load-bearing component*, so the architecture exists primarily to make a non-deterministic component safe, measurable, and economical to run in production.

The concrete difference is everything *around* the model call:

| "AI added in" | AI-First (this project) |
|---|---|
| Call the LLM, use the answer | Sanitize input → call → scan output → guardrail → audit (`src/security.py`, `src/guardrails.py`) |
| Hope it's right | Golden-dataset eval suite with 6 deterministic metrics + 5-dimension LLM-as-judge, regression-gated in CI (`evaluation/`) |
| One model, hard-coded | Provider abstraction + per-provider circuit breaker + per-stage model overrides (`src/tools/`, `src/circuit_breaker.py`) |
| No cost ceiling | Atomic per-user budget reserve/settle via Redis Lua script + rate limits (`src/budget_store.py`) |
| Output trusted as-is | Evidence attached deterministically, not by the model, so it can't be hallucinated |

**Example from this project:** The single most AI-First decision was treating story evidence as non-negotiable provenance. A naive design asks the Story Writer LLM to output the customer quote that justifies each story — which invites fabrication. Instead, the Parser agent extracts quotes once, and `story_writer_agent.py::_attach_evidence()` attaches the quote deterministically by looking up the story's `source_topic_id` against the Parser's output. The model chooses *which* topic a story came from; Python supplies the actual quote. That's AI-First thinking: let the model do the open-ended reasoning, but never let it author the facts a human will audit. We even added `_repair_source_topic_id()` to snap invalid IDs from weaker models back to the nearest real topic by word-overlap similarity.

---

### 2. Describe an AI solution you've built or contributed to. Walk through the key stages from idea to deployment. Where did AI help you the most in the process?

**Backlog Synthesizer**, end to end:

1. **Idea / problem framing.** Discovery calls produce transcripts, wikis, and an existing backlog that nobody reconciles by hand. A single-LLM "extract stories" prompt works on tidy input and collapses on heterogeneous, multi-source input that needs hierarchy *and* gap/conflict detection. That failure mode justified a multi-agent design.

2. **Design.** Five single-responsibility agents (`ParserAgent`, `ConstraintAgent`, `StoryWriterAgent`, `EpicDecomposerAgent`, `GapDetectorAgent`) on a LangGraph `StateGraph` with a typed `PipelineState` (`src/memory/state.py`). The graph has 7 nodes: `initialize → [parse ∥ extract_constraints] → write_stories → decompose_epics → detect_gaps → finalize` — parse and constraint extraction fan out in parallel, then fan in at story writing. Each node is wrapped with an OpenTelemetry span via `_node_with_span()`. Memory is explicit (KV handoff + vector store + audit chain), not implicit.

3. **Build.** Provider-agnostic tool layer (`ClaudeTool`, `GeminiTool`, `OllamaTool`) with a unified `call()` / `call_for_json()` interface; prompts externalized as Markdown in `prompts/`; embedding-based duplicate retrieval runs locally (`all-MiniLM-L6-v2`, no LLM cost) before the pipeline proceeds.

4. **Hardening.** Input sanitizer with 8 prompt-injection detection rules in `src/security.py`; output scanner covering PII (4 patterns), toxicity (3 patterns), and demographic bias (5 patterns); 6 deterministic guardrails; per-provider circuit breaker (CLOSED/OPEN/HALF_OPEN); atomic Redis-backed budget gate with Lua script; concurrency-safe `_merge_dicts` reducer for parallel LangGraph node writes.

5. **Evaluation.** 10 hand-curated golden cases including negative / conflict-heavy / compliance scenarios; 6 deterministic metrics (story count, AC well-formedness, required topics present, forbidden topics absent, expected duplicates found, expected constraint conflicts found) plus a 5-dimension LLM-as-judge; a regression runner that fails CI if any case's score drops ≥ 0.10.

6. **Deploy.** Dockerized with a non-root user on `python:3.11-slim-bookworm`; Prometheus metrics on port 9090, Streamlit UI on port 8501; alert dispatch to Slack, MS Teams, or PagerDuty via `src/alerts.py`; environment-driven config for Azure Container Apps or Kubernetes.

**Where AI helped most:** the genuinely fuzzy, judgment-heavy steps — turning rambling transcript prose into well-formed `Given/When/Then` acceptance criteria, and conflict detection (recognizing that "ship same-day delivery from all stores" contradicts an architecture constraint that only a subset of stores have fulfillment capability). That semantic cross-referencing is exactly what deterministic code is bad at and an LLM is good at. Everything cheap and rule-shaped — tag vocabulary validation (`CANONICAL_TAGS` set with 15 tags), duplicate pre-filtering, evidence attachment — we deliberately kept out of the model.

---

### 3. How do you decide when to use AI versus traditional code for solving a problem? What factors you consider? Give an example of when you chose traditional code over AI, and explain why.

My decision rule is a 2×2 on **(a) is the task fuzzy/semantic or rule-shaped?** and **(b) what's the cost of being wrong?**

- **Semantic + tolerant of variance → AI** (story drafting, conflict reasoning).
- **Rule-shaped + must be exact → traditional code** (this is most of the system).
- **Semantic + must be exact → AI proposes, code/human verifies** (gap detection feeds a human owner; evidence is code-attached).
- **Rule-shaped + tolerant → code**, because it's free and debuggable.

Other factors: **determinism** (anything a reviewer audits should be reproducible), **cost/latency** (every LLM call is money + p95 latency), **testability**, and **security surface** (text reaching the model is an injection surface).

**Example of choosing traditional code — duplicate detection.** The instinct is "ask the LLM which new stories duplicate existing tickets." We rejected that. With N new stories × M existing tickets, an LLM comparison is O(N·M) tokens, slow, costly, and non-reproducible. Instead `src/memory/store.py` embeds tickets locally with `all-MiniLM-L6-v2` (L2-normalized so dot product *is* cosine similarity) and does cosine top-k retrieval (`TOP_K = 5`) to surface candidates — zero LLM cost, deterministic, sub-second — and the LLM never even sees the duplicates path in production. `EmbeddingTool.find_duplicates()` applies a `DEFAULT_DUPLICATE_THRESHOLD = 0.6` floor below which candidates are dropped entirely. We also added `_RETRIEVAL_THRESHOLD = 20`: below 20 tickets we skip embeddings entirely and pass everything through, because the index setup cost isn't worth it. Traditional code does the heavy lifting; the model is reserved for conflicts and gaps where semantic reasoning is genuinely required.

A second example: the 6 guardrail checks in `src/guardrails.py` — AC count range, GWT grammar (`re.compile(r"\b(given|when|then)\b", re.IGNORECASE)`), duplicate titles, canonical tag membership, story grounding, priority rationale length — are plain regex/set-membership, not a second LLM call. Fast, mandatory, and free.

---

### 4. What are some advantages and limitations of using AI to automate tasks compared to writing traditional code? When have you seen each approach work well?

**AI wins when** the input space is open-ended and you can't enumerate the rules: unstructured transcript → structured stories, semantic conflict detection, summarization. Writing those rules by hand is infeasible; the LLM generalizes across phrasing it has never seen.

**AI's limitations:** non-determinism (same input, different output), hallucination, cost and latency per call, a prompt-injection attack surface (we guard against 8 injection patterns in `src/security.py`), and opacity — you can't step through a model's reasoning in a debugger. It degrades silently: a worse answer looks identical to a good one.

**Traditional code wins when** the task is specifiable, must be exact, must be reproducible, or runs hot. It's free per call, unit-testable, and auditable line by line.

**Where I've seen each work well in this project:**
- *AI working well:* the `StoryWriterAgent` turning a vague ask ("associates keep losing the thread during a customer handoff") into a properly-formed user story with `Given/When/Then` acceptance criteria. No rule engine produces that.
- *Code working well:* the tamper-evident audit log (`src/memory/audit_log.py`) — a SHA-256 hash chain (via `_event_hash(prev_hash, event)`) over every agent decision, persisted to SQLite. Compliance demands this be exact and verifiable; an LLM has no business anywhere near it.

The architecture is deliberately a **thin AI core inside a thick deterministic shell**: the model reasons, code validates, audits, bounds, and pays for it.

---

## Technical Depth

### 1. What is context in AI applications, and why does it matter? How do you manage context when building an AI solution? Describe a situation where you had to work with limited context (e.g., token limits) and how you handled it.

**Context** is everything the model can see for one call: system prompt, task instructions, retrieved data, and prior outputs. It matters because an LLM has no memory between calls and a finite window — quality is bounded by what you put in front of it, and both irrelevant context (distraction, cost) and missing context (hallucination) hurt.

**How I manage it here — context is curated, never dumped:**
- **Staged handoff, not full-history replay.** Each LangGraph node reads only the upstream fields it needs from the typed `PipelineState` (`src/memory/state.py`). The `EpicDecomposerAgent` sees *stories*, not the raw transcript. This keeps every call's context minimal and on-task.
- **Retrieval before generation.** The `GapDetectorAgent` doesn't get all M backlog tickets; it gets the top-5 semantically similar candidates from the vector store via `search_similar(query, top_k=5)`. Retrieval is context compression.
- **Pre-flight input guard.** `MAX_INPUT_TOKENS_PER_RUN = 50,000` (estimated at 4 chars/token) rejects oversized input before any spend.
- **Prompt caching of the shared system prompt** (`cache_control: {"type": "ephemeral"}` when the system prompt ≥ 4096 chars, applied when model starts with "claude") so repeated context is not re-billed at full input rate on every one of the pipeline's agent calls.

**Limited-context situation I handled:** a large multi-page transcript plus a full backlog exceeded what I wanted in one Story Writer call. The fix was the handoff architecture itself: the `ParserAgent` collapses the transcript into a bounded list of topics + quotes + summary (using `T-01`…`T-N` IDs), and only that distilled representation flows downstream. Per-agent `max_tokens` is tuned per stage — `StoryWriterAgent` and `GapDetectorAgent` run at 16,000 (they emit the most structured output); `ParserAgent`, `ConstraintAgent`, and `EpicDecomposerAgent` run at 8,000. So instead of one giant context I have five small, purpose-built ones — which is also cheaper and more accurate.

---

### 2. How do you approach building memory or state management in an AI application? Give an example of when your AI solution needed to remember information across interactions.

Three tiers, by lifetime:

1. **Within-run working memory — `PipelineState` (TypedDict).** The single object that flows through the LangGraph graph; each node returns a partial update and LangGraph merges it. The parallel fan-out (`parse` + `extract_constraints`) writes `stage_errors` through a `_merge_dicts` reducer to avoid `INVALID_CONCURRENT_GRAPH_UPDATE`. This is the agents' shared scratchpad.

2. **Cross-run semantic memory — `MemoryStore` (`src/memory/store.py`).** A KV layer for explicit inter-run handoff plus a vector layer. The vector backend is selectable: ChromaDB (`hnsw:space: cosine`) when `USE_CHROMADB=1`, otherwise an NPZ file cache under `.cache/memory/<corpus_hash>.npz`, or in-process numpy for ephemeral runs. With the file cache active, ticket embeddings are computed once and cached — re-running against the same backlog skips the entire embedding step.

3. **Durable audit memory — `AuditLog` (`src/memory/audit_log.py`).** Every agent decision is recorded to a SQLite table (`audit_events`) with timestamps and SHA-256 hash chain (`_event_hash(prev_hash, event)`). `verify_chain()` can cryptographically confirm no entries were tampered with or deleted.

**Example needing memory across interactions:** duplicate-detection workflow. The expensive step is embedding the existing backlog (potentially hundreds of JIRA tickets). On the first run we embed and persist; on every subsequent run against the same corpus, the corpus-hash NPZ cache is hydrated in `hydrate_from_disk()` and we skip straight to retrieval. The system *remembers* the embedded corpus across runs, turning a repeated cost into a one-time one. Separately, the persisted KV `.json` files let a follow-up run hydrate the previous orchestrator's state for inspection and replay — useful when debugging a bad output after the fact.

---

### 3. How do you track what your AI application is doing? Describe your approach to logging, monitoring, and debugging AI components. How do you know when something goes wrong?

Four layers, designed so a failure is *observable*, not silent:

- **Distributed tracing — OpenTelemetry** (`src/telemetry.py`). A root `pipeline.run` span, a `pipeline.node.<name>` span per node (via `_node_with_span()`), an `llm.call` span per provider call carrying tokens in/out and model id, plus child spans for `embedding.index`, `embedding.search`, `story.repair`, and per-guardrail checks (`guardrail.ac_count`, `guardrail.ac_grammar`, etc.). Configured via `OTEL_ENABLED=1`, `OTEL_SERVICE_NAME`, and `OTEL_EXPORTER_OTLP_ENDPOINT`. I can open one trace and see exactly where a run spent time and tokens.

- **Metrics — Prometheus** (`src/metrics.py`, `/metrics` on port 9090): `backlog_active_synthesis` gauge, `backlog_synthesis_duration_seconds` histogram, `backlog_llm_errors_total{provider}` counter, `backlog_synthesis_cost_usd` histogram, `backlog_circuit_breaker_state{provider}` gauge.

- **The tamper-evident audit trail** — the AI-specific debugging artifact. Every agent's decision and reasoning is logged with timestamps and hash-chained to `logs/audit_chain.db`; `render_markdown()` produces a human-readable reasoning chain (`audit_trail.md`). Long prompts and responses (capped at 16 KB each) render in collapsible `<details>` blocks. Live Jira/Confluence fetches are recorded as `live_jira_fetch_ok` / `live_jira_fetch_failed` so each run's data provenance is fully traceable.

- **Structured logs** (`src/logger_setup.py`) — `get_logger(name)` with a unified format string, level overrideable via `LOG_LEVEL` env var.

**How I know something's wrong — three independent signals:**
1. **In-band guardrails** (`src/guardrails.py`) — every run is scanned across 6 checks: AC count, GWT grammar, duplicate titles, non-canonical tags, stories not grounded to a topic, and high-priority stories missing rationale. Findings surface as UI chips and audit events.
2. **Out-of-band evaluation** — the golden-suite regression runner fails CI if any case's score drops ≥ 0.10, catching quality regressions introduced by a prompt change.
3. **Operational alerts** — `backlog_llm_errors_total` spiking, the circuit breaker transitioning to OPEN, or an error-severity security finding all fire to Slack, MS Teams, generic POST, or PagerDuty via `src/alerts.py` (2-second fire-and-forget).

---

### 4. Describe a time when your AI solution produced an incorrect or unexpected result (hallucination). What did you do to identify the problem? What steps did you take to prevent it from happening again?

**The hallucination:** in an early version the `StoryWriterAgent` produced perfectly plausible customer quotes as "evidence" for stories — quotes that were never said. They read exactly like the real transcript, which is the dangerous part: a reviewer would have rubber-stamped a fabricated justification.

**How I found it:** an LLM-as-judge dimension plus a guardrail check in `_check_story_grounding()` that verifies every story's `source_topic_id` points to a real Parser-extracted topic flagged the mismatch — the cited quote didn't exist in any `topics` entry. A golden case with known-correct evidence made the gap measurable.

**Prevention (defense in depth), and this is the key design move:**
1. **Took authorship away from the model.** The `StoryWriterAgent` now only emits a `source_topic_id` (a *choice* among real topic IDs like `T-01`…`T-N`). `_attach_evidence()` looks up the actual `raw_quote` from the Parser's output and attaches it deterministically. The model can no longer invent a quote — it can only point at one that exists.
2. **Self-healing for weak models.** `_repair_source_topic_id()` snaps an invalid ID (e.g., `"T-99"` when only 5 topics exist) to the nearest real topic by word-overlap scoring, records the repair as a `story.repair` span in OTel and an audit event.
3. **A standing guardrail** (`_check_story_grounding()`) so any regression in this class is caught on every run, not just in eval.
4. **Placeholder filtering** in `_attach_evidence()` strips sentinel strings (`"..."`, `"null"`, `"none"`, `"n/a"`, `"tbd"`, `"—"`, `"-"`) before attaching, so a model partial-answer never masquerades as valid evidence.

The general principle: **don't ask the model to produce facts you'll later audit — ask it to select from facts code already owns.**

---

### 5. How do you optimize your AI solution to use fewer tokens or API calls while still getting good results? What techniques have you used?

Concrete techniques in this codebase, roughly in order of impact:

- **Move work off the LLM entirely.** Local embeddings (`all-MiniLM-L6-v2`) do duplicate retrieval for $0; the LLM is not involved in the duplicate-detection path at all in production. `_RETRIEVAL_THRESHOLD = 20` skips embeddings for tiny backlogs where setup cost isn't justified. The 6 guardrails and tag checks are regex/set-membership, not model calls.

- **Prompt caching.** The shared system prompt (loaded from `prompts/system_prompt.md`) is sent with `cache_control: {"type": "ephemeral"}` when the prompt string is ≥ 4096 characters, so it's not re-billed at full input rate across the five agent calls within a run.

- **Curated context, not full history.** Staged handoff means each agent sees only its upstream slice of `PipelineState` — the single biggest token lever, since input tokens dominate cost.

- **Per-stage `max_tokens` tuning.** Output cap is sized to each stage: `StoryWriterAgent` and `GapDetectorAgent` at 16,000 (they emit the most structured JSON); all other agents at 8,000. We don't pay for headroom we never use.

- **Per-stage model overrides.** All stages default to `claude-sonnet-4-5` but each can be independently overridden via the `resolved_models` dict in `PipelineState`. Lighter stages can be pointed at a cheaper model (e.g., a free-tier Gemini Flash variant at $0.10/$0.40 per M tokens) while harder reasoning stages stay on Sonnet.

- **Atomic budget reserve/settle with refund.** `try_reserve(user_id, estimated_cost_usd, daily_limit_usd)` via Redis Lua script atomically gates the run before any spend. `settle_reservation(user_id, actual_cost_usd, reserved_cost_usd)` settles the actual delta — if the run cost less than estimated, the difference is refunded back to the user's daily budget.

- **Retry on transient errors only.** `MAX_RETRIES = 3` with exponential backoff (max 10s) retries *only* `RateLimitError` and `APIConnectionError` — not general `APIError` — so we don't burn tokens retrying non-transient failures.

---

### 6. How do you handle retrieval of chunks in an AI application? Describe your approach to reranking retrieved results and stitching them into a coherent response. What trade-offs do you consider?

Retrieval in this project is the **duplicate-detection path in the `GapDetectorAgent`**: a new story must be checked against an existing backlog of JIRA/GitHub tickets that can run into the hundreds. The pipeline is a **retrieve → score → stitch** with deliberate cost controls at each stage.

**1. Chunking / indexing.** Each existing ticket is one retrieval unit (title + description), embedded with `all-MiniLM-L6-v2` and indexed in the vector store (`src/memory/store.py::index_tickets()`). Embeddings are L2-normalized so dot product *is* cosine similarity, and ChromaDB is configured with `hnsw:space: cosine`. Indexing upserts in chunks of 100 to stay under ChromaDB's batch limits. A ticket is already a clean semantic unit, so I don't sub-split it — over-chunking would fragment a single ticket's meaning across vectors and hurt recall.

**2. Retrieval.** For each new story I build a query string and call `search_similar(query, top_k=5)` (`TOP_K = 5` in `gap_detector_agent.py`). Cosine top-k gives a small candidate set per story instead of scanning the whole backlog.

**3. Scoring and filtering.** `EmbeddingTool.find_duplicates()` applies a `DEFAULT_DUPLICATE_THRESHOLD = 0.6` floor — candidates below this cosine score are dropped entirely. The comment in the code notes this threshold "trades a few extra invocations for materially better recall" — set deliberately generous at 0.6 (down from 0.75 in an earlier iteration) because typical genuine matches score 0.62–0.70. High-confidence matches (above threshold) are recorded as duplicates with zero LLM involvement. This is the entire duplicate-detection path in production.

**4. Stitching into a coherent response.** Retrieved candidates aren't dumped into output — they're folded into the structured synthesis: a duplicate is attached to its matching story with a `confidence` field and related ticket IDs, landing in `synthesis.json` / `synthesis.md` plus the audit trail. The "coherent response" is a structured, traceable artifact — each retrieved-and-kept item carries its provenance (which ticket, what score).

**Trade-offs I weighed:**
- **`top_k` (recall vs. noise/cost).** Larger k catches more true duplicates but sends more candidates through the scoring path. k=5 was the sweet spot for ticket-sized units.
- **Threshold tuning (precision vs. recall).** The 0.6 floor is deliberately generous — I'd rather score a few extra candidates than silently miss a duplicate.
- **Local vs. API embeddings.** Local sentence-transformers means $0 per retrieval, no rate limits, and full reproducibility, at the cost of a slightly weaker embedding than a hosted model — an easy trade for ticket-matching.
- **Skip retrieval when it doesn't pay.** `_RETRIEVAL_THRESHOLD = 20`: under 20 tickets I skip embeddings entirely and pass the full list to the gap-detector prompt, because the index setup cost outweighs the benefit at small scale.
- **Determinism.** The cosine-threshold scoring is reproducible bit-for-bit; the embedding model is pinned to `all-MiniLM-L6-v2` locally so results don't drift with API model updates.

---

## Tooling & SDLC Integration

### 1. Compare 2-3 AI tools or frameworks you've used (e.g., LangChain, LangGraph, simple API calls). What are the main differences? When would you choose one over another?

I've used **raw provider SDK / API calls**, **LangChain**, and **LangGraph**, and this project uses all three at different layers.

- **Raw API calls (Anthropic SDK style).** Maximum control, minimum dependency, easiest to reason about cost and retries. Best for a single bounded call or a thin tool wrapper. I kept the agent-facing interface (`call()` / `call_for_json()`) deliberately SDK-shaped — agents call `claude.call_for_json(prompt, max_tokens=8000)` and don't know or care which provider is underneath.

- **LangChain** (`langchain-anthropic`, `langchain-google-genai`, `langchain-ollama`). Its value here is the **uniform provider abstraction** — one interface over Claude, Gemini, and Ollama with `max_retries` and timeouts wired in. That's what makes the per-provider circuit breaker (`CLAUDE_CB`, `GEMINI_CB` singletons in `src/circuit_breaker.py`) possible without per-provider branching in the agents. I'd choose it when you need provider portability or want batteries-included retries/streaming, and accept some abstraction overhead.

- **LangGraph** (`StateGraph` in `src/pipeline.py`). The orchestration backbone: explicit named nodes, a typed shared state (`PipelineState`), a `_merge_dicts` reducer for concurrent node writes, and `MemorySaver` checkpointing per `thread_id`. I'd choose it (over hand-rolled orchestration or a linear chain) the moment the workflow is a **multi-step graph with state, parallelism, or fan-out** — which is exactly the parse + extract_constraints parallel fan-out feeding into story writing. For a simple two-call linear flow, LangGraph is overkill — plain API calls win.

**Rule of thumb:** raw SDK for a single call; LangChain when you need provider portability; LangGraph when you have a stateful multi-agent graph worth making explicit and observable.

---

### 2. How have you used AI in different parts of the software development process? Pick 2-3 phases (requirements, design, coding, testing, or deployment) and give specific examples of how AI helped.

- **Requirements (the product itself).** Backlog Synthesizer *is* AI applied to the requirements phase: transcripts and wikis in, structured epics/stories/tasks with `Given/When/Then` acceptance criteria + gap/conflict/duplicate detection out. It compresses days of BA reconciliation into a reviewable, traceable artifact with every story grounded to a verbatim customer quote via `_attach_evidence()`.

- **Testing.** AI authored the **golden evaluation harness** strategy and the LLM-as-judge — qualitative scoring across 5 dimensions (AC quality, priority justification, story granularity, tag accuracy, conflict reasoning) that no deterministic assertion can express. AI also generated the adversarial fixtures (negative / conflict-heavy / ambiguity / compliance cases in `evaluation/golden_dataset/`) that hardened the prompts. The judge uses Claude itself, normalizing raw 1–5 scores to [0, 1] via `(score - 1) / 4`.

- **Coding & code review.** AI was used to scaffold the provider tool wrappers, the defensive JSON extraction shared across providers, and for review passes on the 8 injection-detection rules in `src/security.py`. The discipline: AI drafts, the deterministic gates (ruff linting, pytest, OTel span verification) verify. AI assists the reasoning steps; deterministic pipelines verify the output.

---

### 3. Describe an agent pattern or AI workflow you've implemented. How did you structure it? What made it work well (or what challenges did you face)?

**Pattern: Orchestrator + specialized sequential pipeline with parallel fan-out and a shared blackboard.**

One LangGraph `StateGraph` coordinates 7 nodes: `initialize → [parse ∥ extract_constraints] → write_stories → decompose_epics → detect_gaps → finalize` — wrapping 5 single-responsibility agents. `PipelineState` is the shared blackboard; agents communicate by writing typed fields (`topics`, `constraints`, `stories`, `epics`, `gaps`, `conflicts`, `duplicates`), not by calling each other. Each node is instrumented with a `pipeline.node.<name>` OTel span via `_node_with_span()`.

**What made it work:**
- **Single responsibility per agent** — each has one prompt file, one job, one testable contract. Bugs localize to a node.
- **Explicit typed state + per-node OTel spans** — every handoff is inspectable and every step is traced.
- **The deterministic shell** (sanitize → guardrail → audit) around the probabilistic core, so a bad agent output is caught and recorded, not propagated silently.

**Challenges:**
- **Concurrent writes.** Running `parse` + `extract_constraints` as a parallel fan-out triggered LangGraph's `INVALID_CONCURRENT_GRAPH_UPDATE`; solved with a `_merge_dicts` reducer on the `stage_errors` key so both nodes can write safely.
- **Weak-model robustness.** Cheaper models returned malformed JSON or invalid `source_topic_ids`; solved with defensive `call_for_json()` JSON extraction and `_repair_source_topic_id()` self-healing that snaps bad IDs to the nearest real topic by word-overlap scoring and records the repair in the audit log.
- **Provider failure isolation.** A flaky provider could stall runs; solved with per-provider circuit breakers (`CLAUDE_CB`, `GEMINI_CB`) with `CB_FAILURE_THRESHOLD = 3` failures and `CB_RECOVERY_TIMEOUT_SEC = 60.0` — one bad provider trips to OPEN cleanly instead of dragging the pipeline down.

---

### 4. Explain the difference between "Human-in-the-Loop" and "Human-on-the-Loop" in your own words. Give an example of when you'd use each approach in software development.

**Human-in-the-Loop (HITL):** the human is *inside* the execution path — the system pauses and cannot proceed (or commit) without explicit human approval. The human is a required gate.

**Human-on-the-Loop (HOTL):** the system runs autonomously and the human supervises — reviewing outputs, watching dashboards/alerts, able to intervene or roll back, but not blocking each action.

**In this project, both, by blast radius:**

- **HOTL for the synthesis itself.** The 5 agents run end-to-end without pausing; the human reviews the result afterward using the guardrail chips (findings from `src/guardrails.py`), the `audit_trail.md` reasoning chain, and the evaluation dashboard. The output is a *proposal* (a draft backlog) — low cost if imperfect — so blocking on every step would destroy the value. Supervision is the audit trail + evaluation regression dashboard + operational alerts.

- **HITL for the irreversible, outward-facing actions.** Writing back to JIRA (`create_issue` / `publish_synthesis`) and detected conflicts against architecture constraints are routed to a human owner for explicit sign-off before any external write occurs. The cost of a wrong JIRA push or a wrongly-dismissed architectural conflict is high, so a human gate is mandatory there.

**The rule I apply:** HITL when an action is irreversible, outward-facing, or high-consequence; HOTL when output is a reviewable draft and throughput matters. Same system, both patterns, chosen per action's cost of being wrong.
