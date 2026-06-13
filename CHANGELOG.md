# Changelog

The Backlog Synthesizer evolved through several deliberate iterations. Each version is preserved under `versions/` for reviewers who want to diff design decisions across the project's history.

The format roughly follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), with sections grouped by what changed in the multi-agent system.

---

## [Unreleased] — v3 enterprise security & reliability hardening (June 2026)

### Security — Entra ID SSO (complete rewrite of `src/entra_auth.py`)
- **RS256/JWKS signature verification** — replaced insecure base64-decode-only token parsing with full JWT signature verification via `PyJWKClient` + PyJWT. Tokens are now cryptographically verified against Microsoft's JWKS endpoint.
- **CSRF protection via server-side state nonce store** — replaced the fixed `"backlog-synth"` OAuth state string with a per-request UUID nonce stored in a thread-safe in-memory dict (`_STATE_STORE`) with 600-second TTL and single-use consumption.
- **Dynamic config reads** — replaced module-level constants (evaluated once at import time, before `load_dotenv()`) with a `_cfg()` function that reads env vars on every call, preventing Streamlit's module-cache from returning stale empty values.
- **`raise_for_status()`** added to the token exchange HTTP call — HTTP 4xx/5xx responses now surface immediately instead of silently parsing an empty dict.
- **AUTH_DISABLED + ENTRA_TENANT_ID guard** in `app.py` — the app now hard-fails with a clear error if both env vars are set simultaneously (misconfiguration).
- **Missing dependency hard-fail** — `ImportError` on `streamlit-authenticator` now shows a user-facing error and stops the app instead of silently falling through to an admin session.

### Security — Jira tool (`src/tools/jira_tool.py`)
- **Project key validation** — regex `^[A-Z][A-Z0-9]{1,9}$` on startup; invalid `JIRA_PROJECT_KEY` raises `ToolError` immediately rather than producing malformed JQL at query time.
- **Full JQL injection prevention** — search strings now escape `\`, `"`, and `'` before interpolation into JQL queries.

### Reliability
- **Atomic KV writes** (`src/memory/store.py`) — replaced direct `write_text()` with a temp-file + `os.replace()` pattern (POSIX-safe, also works on Windows) preventing partial writes or torn reads on crash.
- **`pipeline_node_span` added to `src/telemetry.py`** — this context manager was referenced by `src/pipeline.py` but missing; the app raised `AttributeError` on any telemetry-instrumented run.
- **`record_stage_tokens` stage param** (`src/telemetry.py`) — removed private `span._name` attribute access (OTel SDK implementation detail); callers now pass `stage` explicitly.
- **`entrypoint.sh` POSIX fix** — removed `exec ... &` (undefined POSIX behavior — `exec` replaces the shell process, `&` forks it); replaced with explicit background fork + `STREAMLIT_PID` tracking. Also removed duplicate warmup step already baked into the Docker image layer.

### Configuration
- **`jira_write_back` default corrected** (`src/feature_flags.py`) — contributor default was `True` but `config/feature_flags.yaml` set it `False`; the in-code default now matches, eliminating silent write-back to Jira for contributor accounts.
- **`requirements-lock.txt` created** — generated via `pip-compile` from `requirements.txt`; all 714 transitive dependencies are pinned for reproducible installs and supply-chain auditability.

### CI / Quality
- **Python version matrix** (`.github/workflows/ci.yml`) — unit tests now run against Python **3.11** and **3.13** in parallel (`fail-fast: false`), catching version-specific incompatibilities before they reach production.
- **Test suite expanded to 205 tests** — 11 new tests in `test_final_round.py` (state nonce lifecycle, HTTP error on token exchange, `pipeline_node_span`, `record_stage_tokens`), 9 new tests in `test_jira_live.py` (project key validation + JQL escaping), 3 new tests in `test_new_modules.py` (atomic KV write). All existing tests updated to mock `_verify_id_token` rather than calling the removed base64 decode path.

### UI / Branding
- **Accenture + NorthStar dual branding** — sidebar shows both the Accenture wordmark and the NorthStar Retail star logo; login page carries the Accenture banner.
- **Demo disclaimer footer** — login page bottom shows "Demo environment — NorthStar Retail Corp is a fictional client created for demonstration purposes only."
- **Duplicate `.empty-state` CSS resolved** (`src/ui/styling.py`) — second definition renamed to `.empty-state.explainer-card`; `app.py` updated to emit both classes, preventing the silent CSS override.

---

## [Unreleased] — submission build

### Added
- **Live Atlassian integration** — `ConfluenceTool` and `JiraTool` both grew a `mode="live"` path. Confluence calls `/wiki/api/v2/pages/{id}`; Jira paginates `/rest/api/3/search/jql` and maps issues into the existing ticket shape. Auto-promotion of `JIRA_*` env vars into `CONFLUENCE_*` so one Atlassian token covers both.
- **Confluence write capability** — `ConfluenceTool.create_page` + `scripts/seed_confluence.py` push the sample wiki content (`samples/architecture_constraints.md`, `samples/product_strategy.md`) into a real space. Used to seed the demo environment.
- **Persistent vector store** — `MemoryStore` writes embedding vectors and KV state to `.cache/memory/` keyed by corpus hash, so re-runs on the same backlog skip the embed step. Toggle via `MEMORY_PERSISTENT=1` or the constructor.
- **Strict PII redaction mode** — `assert_redacted()` + `StrictRedactionViolation` halt the run if any PII pattern slips past the redactor at a tool boundary. Audit-logged.
- **Story evidence** — every story carries a structured `evidence` block linking back to its source topic quote. Evidence is attached deterministically by the system from the cited `source_topic_id` (not produced by the model, so it can't be hallucinated). Rendered inline in the Epics tab as a blockquote with speaker attribution.
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
- **Prompts V2** — all six prompt files rewritten for consistency and robustness: a single `id` field on every artifact (no `topic_id`/`epic_id` drift); explicit modal-verb→severity mapping in the Constraint Extractor; story `evidence` removed from the model's output (now system-attached); the Gap Detector scoped to conflicts + gaps only (duplicates are embeddings-only) with gaps gaining `id` + `related_ids`; re-added worked examples to the Story Writer and Gap Detector. Mocked-test fixtures re-anchored to stable prompt phrases.

### Fixed
- **case_07 (conflict-heavy) regression** — the Parser was dropping requests that were *blocked by a constraint* (it read "PCI forbids that" as the team declining), so conflict-heavy meetings produced zero stories. The Parser prompt now distinguishes "declined" (skip) from "blocked" (keep). case_07 went from 0.33 / 0.00 to 1.00 / 0.90 (deterministic / LLM-judge).
- **Epic Decomposer evidence example** corrected to the real attached shape, preventing the model from "normalizing" evidence and dropping fields.
- Gap Detector now backstops sequential `G-NN` ids, consistent with the other agents.

### Evaluation
- Full golden re-run committed at `evaluation/results/20260601T061247Z/` — deterministic **0.88**, LLM-judge **0.72** across 10 cases (post-fix, V2 prompts). The earlier `20260528T154409Z/` run (0.80 / 0.53) is retained as the pre-fix baseline.
- **Single-prompt baseline** — `evaluation/single_prompt_baseline.py` runs one mega-prompt over the same 10 golden cases for an honest A/B (committed: ~0.84 / 0.86). The honest read: comparable quality on small inputs; the multi-agent edge is structural + duplicate-detection.

### Added — Jira write-back (closes the loop)
- **`JiraTool.create_issue()` + `JiraTool.publish_synthesis()`** create the synthesized backlog in live Jira as **Epic → Story → Sub-task** (acceptance criteria, priority, conflict flags in each description). Progressive fallback (drops `parent`, then `labels`, then issue type) handles differing project configs; partial failures are recorded, not fatal.
- **CLI:** `--publish-jira` / `--no-jira-subtasks` in `src/main.py`.
- **UI:** a **⤴ Create in Jira** button + dialog showing the created issues as clickable links.
- 6 new tests in `tests/test_jira_live.py` (create, fallback, publish, partial-failure). **Total now 128 tests.**

### Added — resilience & vision
- **Provider failover** — `Orchestrator.run(auto_switch=...)`: if a stage's provider fails after retries, it retries on the other provider (Claude↔Gemini). Surfaced as an amber **⚠ FAILOVER** live-log line + a `provider_failover` audit event.
- **Vision auto-switch** — when an image is attached and the Parser is a Gemini model (whose wrapper can't carry images), the Parser is switched to `claude-sonnet-4-5` so the image is actually read.
- Both are gated by a sidebar **"Auto-switch model"** toggle (default off → exact preset honoured; on → failover + vision-switch, every switch shown in the log/audit).
- **Whiteboard vision sample** — `samples/whiteboard_sprint_planning.png` (generated by `scripts/make_whiteboard_sample.py`), selectable directly in the vision picker.

### Added — UI/UX
- **Multi-select sources** — the transcript / wiki / backlog pickers are `st.multiselect`; multiple bundled samples are combined into one source (transcripts/wikis concatenated, backlogs merged).
- **Always-visible multi-file upload** — each picker has an "Upload your own" expander accepting multiple files; uploads + samples are merged.
- **Top-right nav** — Home / History / Help (always) + Export / Create-in-Jira (after a run); a "How it works" dialog.
- **Accumulating live log** — per-agent events now append (each agent's lines persist) instead of overwriting, with an end-of-run summary, a toast, and a persistent failover/failure banner.
- **Accenture branding** — sidebar wordmark + a "demo on mock data · fictional client NorthStar Retail" footer; cyan primary colour; subtle multi-select chips.
- **Tooling** — a `Makefile` (`make help/test/demo/ui/eval/…`) and captured UI screenshots under `docs/screenshots/` (referenced by the README Demo section).

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

## v1 — earlier multi-agent snapshot (`versions/v1_baseline/`)

This is a frozen earlier snapshot of the **same five-agent architecture** (Parser → Constraint Extractor → Story Writer → Epic Decomposer → Gap Detector, with `memory/` and `audit_log.py`). It is *not* a single-agent system — the name is historical. For a genuine single-mega-prompt comparison, see `evaluation/single_prompt_baseline.py` and Appendix D of `docs/TECHNICAL_DOCUMENT.md`.

### Present in this snapshot
- The full five-agent pipeline, shared `MemoryStore`, and append-only `AuditLog`
- File-based input loader (txt, md, pdf, json); JSON + Markdown output formatter
- Sample data: NorthStar Retail Q3 meeting transcript, architecture constraints wiki, 30-ticket Jira backlog
- 4 golden cases with deterministic metrics

### What it lacks vs. the current build (added later)
- Live Jira / Confluence integration, Confluence write-back, and one-token auth promotion
- Vision input, compare-mode, persistent vector cache, strict PII redaction
- Output guardrails, LLM-as-judge, regression dashboard, A/B prompt comparison, 6 extra golden cases
- The V2 prompts (single-`id` schema, system-attached evidence, embeddings-only duplicates)

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
