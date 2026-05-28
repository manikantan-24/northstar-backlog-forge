# Changelog

The Backlog Synthesizer evolved through several deliberate iterations. Each version is preserved under `versions/` for reviewers who want to diff design decisions across the project's history.

The format roughly follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), with sections grouped by what changed in the multi-agent system.

---

## [Unreleased] — submission build

### Added
- **Live Atlassian integration** — `ConfluenceTool` and `JiraTool` both grew a `mode="live"` path. Confluence calls `/wiki/api/v2/pages/{id}`; Jira paginates `/rest/api/3/search/jql` and maps issues into the existing ticket shape. Auto-promotion of `JIRA_*` env vars into `CONFLUENCE_*` so one Atlassian token covers both.
- **Confluence write capability** — `ConfluenceTool.create_page` + `scripts/seed_confluence.py` push the sample wiki content (`samples/architecture_constraints.md`, `samples/product_strategy.md`) into a real space. Used to seed the demo environment.
- **Persistent vector store** — `MemoryStore` writes embedding vectors and KV state to `.cache/memory/` keyed by corpus hash, so re-runs on the same backlog skip the embed step. Toggle via `MEMORY_PERSISTENT=1` or the constructor.
- **Strict PII redaction mode** — `assert_redacted()` + `StrictRedactionViolation` halt the run if any PII pattern slips past the redactor at a tool boundary. Audit-logged.
- **Story evidence** — every story carries a structured `evidence` block linking back to the parser-extracted source quote. Rendered inline in the Epics tab as a blockquote with speaker attribution.
- **LLM-as-judge evaluation** — the previously-stubbed `evaluation/llm_as_judge.py` is now fully wired into `run_evaluation.py` via `--use-llm-judge`. Scores normalised to [0, 1] across five qualitative dimensions.
- **Regression dashboard** — `evaluation/dashboard.py` aggregates results across runs and surfaces cases whose deterministic score dropped ≥ 0.10 vs. the previous run.
- **A/B prompt comparison** — `evaluation/ab_compare.py` swaps a candidate prompt in, runs the golden suite, then restores the original. Per-case deltas + verdict.
- **6 new golden cases** (5-10) — empty, all-duplicates, conflict-heavy, ambiguous, multi-team, compliance. Total golden dataset now 10 cases.
- **Cost panel** — per-agent token + cost breakdown rendered as a bar chart; cost trend across the last ten runs as a line chart.
- **Output guardrails** — post-LLM heuristic checks (`src/guardrails.py`) verify story grounding, AC count, tag canonicality, and unique titles. Failures recorded in audit log; non-blocking.
- **Vision input** — vision-capable Claude / Gemini models accept PNG/JPG uploads (whiteboard photos, screenshots). Wired into the sidebar uploader.
- **Compare-mode** — orchestrator now supports running two LLM providers in parallel and surfacing the side-by-side diff in the Epics tab.
- **Home-screen explainer** — replaces the previous "Selected inputs" echo block with a 3-step onboarding card and a large centered SYNTHESIZE CTA on the main canvas.
- **Pre-run cost estimate** — sidebar shows an estimated USD spend based on input size and active stage models before the user clicks Synthesize.
- **Streaming progress** — per-stage elapsed timer in the live progress strip.
- **Stage animations** — fade-in on first render, sweep bar on the active stage, scale-pop on transition to done. High-priority chip pulses gently.
- **Live audit events** — `live_confluence_fetch_ok`, `live_jira_fetch_ok`, `strict_redact_violation`, `pii_redacted` events captured in the audit trail for run provenance.
- **CI workflow** — runs unit tests + (gated) evaluation suite with LLM-as-judge + regression dashboard; uploads results as artifacts.

### Changed
- `Orchestrator.run` accepts `strict_redact`, `persistent_memory`, `live_confluence_page_id`, `live_jira`, `vision_image_path`, and `compare_models` kwargs without breaking the existing default-args contract.
- `requirements.txt` adds `requests`, `pandas` (for cost-panel charts), and annotates `chromadb` / `pypdf` for clarity.
- Streamlit CSS extended with empty-state + main-CTA + stage-animation rules. Existing classes preserved.

### Documentation
- `README.md` — new "Optional capabilities" section, evaluation usage, A/B compare instructions.
- `.env.example` — Confluence env vars, persistent-memory toggle, live-mode flags.
- `PRODUCTION_READINESS.md` — P0/P1/P2 gap analysis with effort estimates (new).
- `CHANGELOG.md` — this file (new).

---

## v2 — UI polish (`versions/v2_ui_polish/`)

### Added
- Streamlit UI with five-agent pipeline visualisation
- Sidebar per-stage model picker (Free / Balanced / Premium presets + custom)
- Run history dialog with side-by-side compare
- Cost panel showing per-agent input/output tokens and estimated USD
- Duplicate-compare modal with word-level diff highlighting
- Output editor — flat data editor for stories, with round-trip save back into the epic structure

### Changed
- Single-prompt v1 replaced by the bounded five-agent pipeline (Parser → Constraint Extractor → Story Writer → Epic Decomposer → Gap Detector)
- Memory becomes explicit `MemoryStore` (KV + vector) shared across agents
- Audit log becomes structured (`AuditLog.record`) with reasoning per event

### Documentation
- `architecture.md` — Mermaid diagram, agent roles, memory contracts, retry policy
- `docs/AGENT_DESIGN.md` — rationale for the bounded pipeline vs. an autonomous loop
- `docs/PROMPT_ENGINEERING.md` — iteration history across the five prompts
- `docs/AI_USAGE_SDLC.md` — how AI was used in each SDLC stage (problem framing, design, implementation, evaluation, documentation)

---

## v1 — baseline single-agent (`versions/v1_baseline/`)

### Added
- Single LLM call processes the entire input (transcript + wiki + backlog) into a synthesis
- File-based input loader (txt, md, pdf, json)
- Output formatter writing `synthesis.json` + `synthesis.md`
- Sample data: NorthStar Retail Q3 meeting transcript, architecture constraints wiki, 30-ticket Jira backlog
- 4 golden cases with deterministic metrics

### Limitations (closed in v2)
- No memory between processing stages — the model re-derived everything from the prompt on every call
- No audit log — only the final synthesis was inspectable
- No tag canonicalisation — tags drifted between runs
- No conflict detection between new stories and architecture constraints

---

## Decisions worth recording

These aren't features but explain why the project looks the way it does.

- **Bounded five-stage pipeline, not autonomous agents.** Reproducibility, testability, and a cost cap. See `docs/AGENT_DESIGN.md`.
- **Mock-by-default tool integrations.** Tests and CI never spend API credit. Live mode opt-in.
- **`requests` over the official Atlassian SDK.** The SDK adds a heavy dependency for two endpoints we hit.
- **In-process embeddings via sentence-transformers, with optional persistence.** ChromaDB is in `requirements.txt` for the eventual swap but not yet wired — see `PRODUCTION_READINESS.md` item 11.
- **`.env` over a config file.** Cleaner for local dev, plays nicely with twelve-factor deploy targets.

---

## Versioning

This repository follows the `versions/<vN_label>/` convention from the reference UI-Smart-Backlog-Assistant project. Each snapshot is frozen at the point of a meaningful design decision so reviewers can diff design choices across iterations without trawling git history.
