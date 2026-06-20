# Backlog Synthesizer

> 🚀 **Live Demo:** [Access the live Azure-deployed application](https://backlog-synthesizer.happyforest-744d2ef4.eastus.azurecontainerapps.io) (Secured via Microsoft Entra ID SSO).

> **Version:** V2

A multi-agent AI system that ingests customer meeting transcripts, architecture wikis, and existing engineering backlog tickets — and synthesizes the result into a structured set of epics, user stories, and tasks. Detects gaps, conflicts, and duplicates. Maintains a tamper-evident audit trail of every agent decision.

Built as a demonstration of practical AI-First engineering — five single-responsibility agents on a **LangGraph StateGraph**, multi-provider LLM layer (Claude / Gemini / Ollama) behind a circuit breaker, vector memory, deterministic security shell, and a regression-gated evaluation harness.

The bundled sample data is themed around **NorthStar Retail**, a fictional national retail giant with ~2,000 stores spanning grocery, electronics, apparel, home goods, pharmacy, and auto service.

---

## Demo

The application can be run locally using the Streamlit UI. 
Run the following command to start the web application:
```bash
make ui        # or: streamlit run app.py
```
This launches a browser interface where you can upload transcripts, configure constraints, select models (Claude, Gemini, or Ollama), track the 5-agent pipeline execution in real-time, inspect audit trails, and push issues to Jira.

Or run it yourself in one command: `make demo` (CLI) or `make ui` (web).

---

## What it does

Feed it any combination of these:

- **Customer / stakeholder meeting transcripts** (`.txt`, `.md`, `.pdf`)
- **Whiteboard planning photos & UI sketches** (JPEG, PNG, WebP — processed via multimodal vision APIs to extract requirements or wireframe details)
- **Architecture / wiki exports** describing constraints, integrations, platform limits (`.md`)
- **Existing backlog tickets** from Jira or GitHub Issues (live API via MCP, or mocked JSON)

Get back a structured synthesis:

- **Epics** — high-level themes (e.g., "Loyalty Program Modernization")
- **Stories** — user stories under each epic with full acceptance criteria in Given/When/Then form
- **Tasks** — concrete implementation steps under each story
- **System / feature tags** — `mobile-app`, `pos`, `loyalty`, `inventory`, etc. (15-tag canonical vocabulary)
- **Gaps** — capabilities the requirements imply but the existing backlog hasn't planned
- **Conflicts** — new requests that contradict architectural constraints or existing in-flight work
- **Duplicates** — new requests that overlap with items already in Jira / GitHub (local embedding-based, $0)

Outputs are written to `outputs/<timestamp>/` as `.json` (machine-readable), `.md` (human-shareable), and `audit_trail.md` (every agent decision, SHA-256 hash-chained).

---

## Why this exists (vs. the simpler single-agent version)

The simpler single-agent version works for tidy inputs. It breaks down when:

- Inputs come from **multiple heterogeneous sources** (a transcript, a Confluence page, a Jira export)
- The output needs **hierarchy** (epics → stories → tasks), not a flat list
- Detection has to span beyond duplicates to **gaps and constraint conflicts**
- You need to **show your reasoning** for compliance / handoff to a human owner
- The model must not be able to **fabricate evidence** — customer quotes must be verifiable

A multi-agent design lets each agent do one thing well, write its intermediate findings to a shared typed state (`PipelineState`), and let downstream agents reason from that. Evidence is attached **deterministically** by `_attach_evidence()` — the model selects a topic ID, Python supplies the actual quote — so it can't be hallucinated.

---

## Architecture (at a glance)

A **LangGraph StateGraph** with 7 nodes orchestrates five single-responsibility agents. `parse` and `extract_constraints` run in parallel (fan-out); all others are sequential. Every agent writes to a shared `PipelineState` (TypedDict) and the SHA-256-chained audit log.

```
                     ┌─────────────────────┐
                     │     initialize      │
                     └──────────┬──────────┘
                                │
               ┌────────────────┴────────────────┐
               ▼  (parallel fan-out)              ▼
       ┌──────────────┐                 ┌────────────────────┐
       │    parse     │                 │ extract_constraints│
       │ (Parser      │                 │ (Constraint        │
       │  Agent)      │                 │  Agent)            │
       └──────┬───────┘                 └─────────┬──────────┘
              └──────────────┬──────────────────────┘
                             ▼  (fan-in)
                    ┌─────────────────┐
                    │  write_stories  │
                    │ (StoryWriter    │
                    │  Agent)         │
                    └────────┬────────┘
                             ▼
                    ┌─────────────────┐
                    │ decompose_epics │
                    │ (EpicDecomposer │
                    │  Agent)         │
                    └────────┬────────┘
                             ▼
                    ┌─────────────────┐
                    │  detect_gaps    │
                    │ (GapDetector    │
                    │  Agent)         │
                    └────────┬────────┘
                             ▼
                    ┌─────────────────┐
                    │    finalize     │
                    │ (guardrails +   │
                    │  audit chain)   │
                    └─────────────────┘

         All agents share PipelineState + AuditLog
         All LLM calls wrapped: InputSanitizer → call → OutputScanner
```

See [architecture.md](architecture.md) for the full Mermaid diagrams (Application & AI Layer, Infrastructure & Deployment, Agent Pipeline sequence, Security data flow, Evaluation harness).

---

## Setup

### Prerequisites

- Python 3.11+ (3.13 also tested in CI)
- An Anthropic API key (`ANTHROPIC_API_KEY`)

### Installation

```bash
cd backlog-synthesizer
python3 -m venv venv
source venv/bin/activate

# Pinned deps — reproducible installs (recommended):
pip install -r requirements-lock.txt

# Unpinned — faster dev iteration:
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY
```

All available environment variables are documented in [`.env.example`](.env.example).

### Run the bundled sample (CLI)

```bash
python src/main.py \
    --transcript samples/meeting_notes.txt \
    --constraints samples/architecture_constraints.md \
    --backlog samples/jira_backlog.json
```

### Run the compliance & conflict demo (CLI)

To demonstrate how the **Constraint Agent** catches critical security and architecture violations (such as data residency issues, plain-text credit card caching, and custom cryptography rules):
```bash
python src/main.py \
    --transcript samples/vendor_security_proposal.md \
    --constraints samples/architecture_constraints.md
```

Outputs land in `outputs/<timestamp>/`:
- `synthesis.json` — full structured result
- `synthesis.md` — human-readable Markdown
- `audit_trail.md` — every agent decision with timestamps and reasoning

### Run the Streamlit UI

```bash
make ui        # or: streamlit run app.py
```

Open `http://localhost:8501`. The UI supports:
- Upload transcript / wiki / backlog (file or paste)
- Live Atlassian sources (toggle Confluence page ID + Jira project key)
- Per-stage model selection (Claude / Gemini / Ollama)
- Real-time pipeline progress with per-node status
- Epics → stories → tasks with expandable evidence panel
- Guardrail findings chips (error / warn / info)
- Cost panel (per-stage tokens, per-agent USD, 10-run trend chart)
- Downloadable `synthesis.json`, `synthesis.md`, `audit_trail.md`
- Human-in-the-loop Jira push gate (admin / contributor only)
- Two-way Jira status sync
- Interactive Cryptographic Audit Log Verification (admin only)

### Run the tests

```bash
pytest tests/ -v
```

**318 tests across 14 files**, all mocked end-to-end (zero API credit, ~1s):

| File | What it covers |
|---|---|
| `test_agents.py` | Per-agent unit tests + MemoryStore / AuditLog |
| `test_orchestrator.py` | Five-agent handoff + output formatter |
| `test_redactor.py` | PII redaction / restoration |
| `test_guardrails.py` | Six deterministic guardrail checks |
| `test_compare_mode.py` | A/B prompt comparison |
| `test_jira_live.py` | JQL injection escaping, project key validation, write-back |
| `test_confluence_live.py` | Confluence API integration |
| `test_vision.py` | Multimodal (image) input handling |
| `test_evaluation_runner.py` | Evaluation harness |
| `test_final_round.py` | Entra SSO nonce lifecycle, RS256 verification, OTel spans |
| `test_new_modules.py` | Atomic KV write, circuit breaker, GDPR purge, metrics |
| `test_hallucination.py` | Evidence grounding + `_repair_source_topic_id()` |
| `test_load_soak.py` | Concurrency + stress tests |
| `test_security_circuit_breaker.py` | InputSanitizer, OutputScanner, CircuitBreaker states |

CI runs the full suite on Python 3.11 and 3.13 in parallel (`fail-fast: false`).

### Run the evaluation harness

```bash
# Deterministic metrics only
python evaluation/run_evaluation.py

# + LLM-as-judge (qualitative scoring across 5 dimensions)
python evaluation/run_evaluation.py --use-llm-judge

# Single golden case
python evaluation/run_evaluation.py --case case_07

# Regression dashboard across past runs
python evaluation/dashboard.py --fail-on-regression --regression-threshold 0.10
```

Results land in `evaluation/results/<timestamp>/` (per-case scorecards + `summary.json`). CI fails if any case's deterministic score drops ≥ 0.10.

### A/B compare two prompt variants

```bash
python evaluation/ab_compare.py \
    --prompt prompts/parser_prompt.md \
    --variant prompts/experiments/parser_prompt_v2.md \
    --use-llm-judge
```

Runs the full golden suite twice, reports per-case deltas, writes `report.json` under `evaluation/results/ab/`.

---

## Enterprise SSO (Microsoft Entra ID)

Set the following env vars to enable Entra ID authentication:

```bash
ENTRA_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
ENTRA_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
ENTRA_CLIENT_SECRET=your-client-secret
ENTRA_REDIRECT_URI=http://localhost:8501/
```

`src/entra_auth.py` handles the full OAuth2 authorization code flow:
- RS256 JWT verification via Microsoft JWKS endpoint
- Per-request state nonces with 600s TTL (CSRF protection)
- Role mapping from Entra app roles → `viewer` / `contributor` / `admin`
- Role-Based Access Control (RBAC): Entra ID App Roles map directly to application roles, governing permissions for viewing (viewer), running the synthesis pipeline (contributor), or overriding safety limits, editing features, and performing cryptographic log verification audits (admin)

Leave these vars unset to fall back to local `config/auth.yaml` username/password auth.

---

## MCP Server

The pipeline is also exposed as a **Model Context Protocol server** (`mcp_server.py`) using FastMCP, so Claude Desktop or any MCP-capable agent can drive it as a tool:

```bash
python mcp_server.py
```

Exposes five tools: `synthesize_backlog`, `preview_prompts`, `get_run_history`, `push_to_jira`, `get_audit_trail`.

---

## Deployment

### Azure (Container Apps)

```bash
# Provision infrastructure (first time)
gh workflow run terraform.yml -f action=apply -f environment=staging

# Deploy a new image
git push origin main   # triggers deploy.yml automatically
```

Resources provisioned by `infra/terraform/`: Azure Container Registry, Container Apps, Key Vault (10 secrets via MSI), Azure Cache for Redis (Basic C0, atomic budget enforcement), Azure Files (logs/ 10 GB + outputs/ 50 GB), Log Analytics Workspace.

See [target_architecture_comparison.md](target_architecture_comparison.md) for a detailed comparison of the current local state vs. the target production architecture.

---

## Optional capabilities

- **PDF transcripts** — `--transcript meeting.pdf` works out of the box (text-extractable PDFs via pypdf).
- **Live Atlassian sources** — fill in `JIRA_*` + `CONFLUENCE_*` in `.env`. Toggle in the UI sidebar or pass `--live-jira` / `--confluence-page-id 65830` on the CLI. Successes and failures are recorded in the audit trail as `live_jira_fetch_ok` / `live_confluence_fetch_ok` (or `_failed`).
- **Seed live sources** — `scripts/seed_jira.py`, `scripts/seed_confluence.py`, `scripts/seed_github_issues.py` populate the NorthStar Retail sample data into your real instances.
- **Persistent vector memory** — `MEMORY_PERSISTENT=1` caches ticket embeddings under `.cache/memory/`. Re-runs on the same backlog skip the embed step entirely.
- **Strict PII redaction** — `strict_redact=True` replaces email, phone, SSN, card numbers, and names with tokens (`[EMAIL_1]`, `[PHONE_1]` etc.) before any LLM call and restores them in the final output. Raw PII never reaches the model.
- **Multi-provider LLM** — set `GOOGLE_API_KEY` to enable Gemini (free-tier Flash variants available). Set `OLLAMA_BASE_URL` for a local Ollama instance ($0/call). All three providers are hot-swappable per stage via `resolved_models` in `PipelineState`.
- **Cost panel** — every UI run shows per-stage tokens, per-agent USD cost, and a 10-run trend chart.
- **Story evidence** — each story carries the customer quote that motivated it (`story.evidence[0].raw_quote`). Evidence is attached **deterministically** from the parser's output by `_attach_evidence()` — not generated by the model — so it can't be hallucinated.

---

## How AI is used

The LLM is called by each agent for its specific reasoning task. Outside those calls, everything is deterministic Python — input sanitization, output scanning, guardrails, duplicate detection, evidence attachment, and audit logging are all code.

| Agent | What it reasons about | LLM | Other tools |
|---|---|---|---|
| **Parser** | Topics + customer quotes in the transcript | Claude / Gemini / Ollama | Vision (image inputs) |
| **Constraint Extractor** | Architectural rules, limits, forbidden patterns | Claude / Gemini / Ollama | `MCPConfluenceTool` (live) |
| **Story Writer** | User stories + GWT acceptance criteria | Claude (default) | `_attach_evidence()` (deterministic) |
| **Epic Decomposer** | Group stories into epics + task breakdown | Claude / Gemini / Ollama | — |
| **Gap Detector** | Conflicts + gaps (duplicates are local, $0) | Claude (default) | `MCPJiraTool`, `MCPGithubTool`, `EmbeddingTool` |

Duplicate detection uses local `all-MiniLM-L6-v2` embeddings (cosine top-5, threshold 0.6) — no LLM call, no API cost.

---

## Project structure

```
backlog-synthesizer/
├── README.md
├── TECHNICAL_GUIDE.md           ← Complete architecture, implementation & interview guide
├── target_architecture_comparison.md ← Comparison of local and production architectures
├── PRODUCTION_READINESS.md
├── architecture.md              ← Mermaid diagrams (App+AI layer, Infra, Pipeline, Security, Eval)
├── Makefile
├── requirements.txt
├── requirements-lock.txt
├── .env.example
├── Dockerfile
├── .dockerignore
├── app.py                       ← Streamlit UI entry point
├── mcp_server.py                ← FastMCP server (5 tools)
├── entrypoint.sh
├── start.sh                     ← Helper script to launch Streamlit + Ollama
│
├── src/
│   ├── pipeline.py              ← LangGraph StateGraph (7 nodes)
│   ├── orchestrator.py          ← multi-agent coordinator
│   ├── main.py                  ← CLI entry point
│   ├── input_loader.py
│   ├── output_formatter.py
│   ├── security.py              ← InputSanitizer (8 rules) + OutputScanner (PII/toxicity/bias)
│   ├── redactor.py              ← PII redactor (matches regexes and restores safely)
│   ├── guardrails.py            ← 6 deterministic post-synthesis checks
│   ├── circuit_breaker.py       ← per-provider CLOSED/OPEN/HALF_OPEN breaker
│   ├── budget_store.py          ← Redis atomic reserve/settle + rate limiting
│   ├── rate_limiter.py
│   ├── entra_auth.py            ← Microsoft Entra ID OAuth2/OIDC
│   ├── feature_flags.py
│   ├── telemetry.py             ← OpenTelemetry spans + metrics
│   ├── metrics.py               ← Prometheus metrics (:9090)
│   ├── pricing.py               ← per-model cost estimates
│   ├── alerts.py                ← Slack / MS Teams / PagerDuty
│   ├── gdpr_purge.py            ← GDPR right-to-be-forgotten utility (purges ticket records from state/embeddings)
│   ├── ollama_manager.py        ← Lifecycle manager for local Ollama process
│   ├── logger_setup.py
│   └── startup_check.py
│   ├── agents/
│   │   ├── base.py
│   │   ├── parser_agent.py
│   │   ├── constraint_agent.py
│   │   ├── story_writer_agent.py    ← _attach_evidence(), _repair_source_topic_id()
│   │   ├── epic_decomposer_agent.py
│   │   └── gap_detector_agent.py
│   ├── tools/
│   │   ├── base.py
│   │   ├── claude_tool.py
│   │   ├── gemini_tool.py
│   │   ├── ollama_tool.py
│   │   ├── embedding_tool.py        ← all-MiniLM-L6-v2, local duplicate detection
│   │   ├── jira_tool.py
│   │   ├── confluence_tool.py
│   │   ├── github_tool.py
│   │   ├── mcp_atlassian_tool.py    ← live Jira + Confluence via MCP
│   │   └── mcp_github_tool.py       ← live GitHub Issues via MCP
│   ├── memory/
│   │   ├── state.py                 ← PipelineState TypedDict + _merge_dicts reducer
│   │   ├── store.py                 ← KV + ChromaDB / NPZ vector cache
│   │   └── audit_log.py             ← SHA-256 hash chain, SQLite
│   └── ui/
│       ├── run_history.py
│       ├── styling.py
│       └── cost.py
│
├── prompts/
│   ├── system_prompt.md
│   ├── parser_prompt.md
│   ├── constraint_extractor_prompt.md
│   ├── story_writer_prompt.md
│   ├── epic_decomposer_prompt.md
│   └── gap_detector_prompt.md
│
├── evaluation/
│   ├── golden_dataset/              ← 10 cases (negative, conflict-heavy, compliance)
│   ├── metrics.py                   ← 6 deterministic metrics
│   ├── llm_as_judge.py              ← 5-dimension qualitative scoring, normalised [0,1]
│   ├── run_evaluation.py
│   ├── dashboard.py                 ← regression dashboard, --fail-on-regression
│   └── ab_compare.py                ← A/B prompt variant comparison
│
├── tests/                           ← 318 tests across 14 files (all mocked, ~1s)
│
├── samples/                         ← NorthStar Retail demo dataset
│   ├── meeting_notes.txt
│   ├── architecture_constraints.md
│   ├── product_strategy.md
│   ├── vendor_security_proposal.md  ← Vendor proposal with deliberate conflicts & violations
│   ├── jira_backlog.json            ← 30 tickets with intentional overlaps + conflicts
│   └── github_issues.json
│
├── infra/
│   └── terraform/                   ← Azure IaC (azurerm ~3.100)
│
├── .github/
│   └── workflows/
│       ├── ci.yml                   ← tests + lint + eval gate
│       ├── deploy.yml               ← Azure Container Apps deploy
│       └── terraform.yml            ← Azure infrastructure
│
├── scripts/
│   ├── azure_setup.sh
│   ├── seed_jira.py
│   ├── seed_confluence.py
│   ├── seed_github_issues.py
│   ├── demo_hallucination.py
│   ├── test_mcp_tools.py
│   ├── capture_screenshots.py       ← Local tool for demo capture
│   └── make_whiteboard_sample.py    ← Helper to generate mock image inputs
│
└── config/
    └── auth.yaml                    ← local fallback auth (gitignored)
```

---

## License

MIT — use freely.
