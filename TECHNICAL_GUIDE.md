# NorthStar Backlog Forge — End-to-End Technical Guide
### Complete Architecture, Implementation & Interview Reference

---

## Table of Contents

1. [Project Overview](#part-1--project-overview)
2. [System Architecture](#part-2--system-architecture)
3. [Five Specialist Agents](#part-3--five-specialist-agents)
4. [Pipeline Orchestration](#part-4--pipeline-orchestration)
5. [AI Tools Layer](#part-5--ai-tools-layer)
6. [Jira & Confluence Integration](#part-6--jira--confluence-integration)
7. [Security Layer](#part-7--security-layer)
8. [Memory & Audit Trail](#part-8--memory--audit-trail)
9. [Microsoft Entra SSO](#part-9--microsoft-entra-sso)
10. [Web UI Internals](#part-10--web-ui-internals)
11. [Docker & Azure Deployment](#part-11--docker--azure-deployment)
12. [Observability](#part-12--observability)
13. [Cost & Budget Model](#part-13--cost--budget-model)
14. [Error Handling & Resilience](#part-14--error-handling--resilience)
15. [How AI Was Used to Build This](#part-15--how-ai-was-used-to-build-this)
16. [Interview Q&A](#part-16--interview-qa)

---

## Part 1 — Project Overview

### 1.1 What the tool does

NorthStar Backlog Forge transforms unstructured engineering inputs into a structured, audited sprint backlog. It accepts any combination of:

- **Meeting transcripts** (`.txt`, `.md`, `.pdf`)
- **Whiteboard / architecture photographs** (JPEG, PNG, WebP — vision inputs via Claude's multimodal API)
- **Confluence architectural wiki pages** — fetched live by page ID or provided as a local file
- **Existing Jira backlog** — fetched live by project key or from a local JSON fixture

And produces:

- **Epics** — thematic groupings with titles and descriptions
- **User Stories** — `As a / I want / so that` format, with Given/When/Then acceptance criteria, priority, priority rationale, and traceable evidence (the raw quote and speaker from the meeting that motivated each story)
- **Tasks** — typed implementation sub-tasks inside each story (`backend | frontend | data | infra | qa | spike`)
- **Gaps** — requirements clearly implied by the inputs but not yet written as stories
- **Conflicts** — stories that violate architectural constraints (`must` / `forbidden` rules)
- **Duplicates** — new stories that closely match existing Jira tickets (semantic embedding search + LLM judgment)
- **Audit Trail** — every LLM prompt, every response, every decision, with SHA-256 chain fingerprinting stored in SQLite

### 1.2 The business context (NorthStar Retail)

The system prompt establishes the agent persona as an **experienced agile delivery lead at NorthStar Retail's engineering organization**. NorthStar runs approximately 2,000 supermarkets and big-box stores across the US, spanning grocery, electronics, apparel, home goods, pharmacy, and auto service.

The platforms the agents reason about: **POS, mobile app, e-commerce, loyalty, inventory, pharmacy fulfillment, vendor portal, store-associate tools**. This domain specificity matters — agents given retail context write more precise acceptance criteria ("the POS must process cash sales during WAN outages") than agents working in generic software.

### 1.3 Why five agents instead of one prompt

Early single-prompt prototypes failed in three consistent ways:

1. **Deduplication was skipped** on backlogs with more than ~50 tickets — the model's attention was consumed by story generation and it forgot to cross-reference.
2. **Priority scores drifted** — stories ranked #14 in the output were always "Medium" regardless of content.
3. **Evidence was lost** — stories were generated correctly but disconnected from the source quotes that justified them.

Splitting into five specialist agents, each with exactly one reasoning task, solved all three problems. The cost is extra API calls; the benefit is dramatically better output quality and full traceability.

### 1.4 What makes this an enterprise-grade system

- **Microsoft Entra ID SSO** with RS256 JWT verification and stateless HMAC-signed CSRF tokens
- **Per-user rate limiting** (runs/hour and USD/day) backed by Azure Cache for Redis
- **PII redaction** applied before any LLM sees the transcript, with an optional strict mode that halts on violation
- **Prompt injection scanning** across 8 attack categories before any user text reaches the models
- **Tamper-evident audit trail** — SHA-256 hash chain across all pipeline events, persisted to SQLite
- **Circuit breakers** per LLM provider — auto-failover from Claude → Gemini → Ollama → Claude
- **OpenTelemetry spans** exported to Grafana Cloud Tempo/Mimir; Prometheus metrics on `:9090`
- **Azure Files shares** for persistent log and output storage across Container App replicas
- **Terraform-managed infrastructure** with Azure Key Vault for all secrets

---

## Part 2 — System Architecture

### 2.1 The five-stage pipeline

```
                     START
                       │
              ┌────────▼────────┐
              │   initialize    │
              │ (fetch data,    │
              │  scan injections│
              │  resolve models)│
              └────────┬────────┘
                       │
         ┌─────────────┴─────────────┐
         ▼                           ▼
   ┌───────────┐             ┌──────────────────┐
   │  Parser   │             │ Constraint        │
   │ (Agent 1) │             │ Extractor         │
   │  Topics   │             │ (Agent 2)         │
   │  from     │             │ Rules from wiki   │
   │ transcript│             │ (runs in parallel)│
   └─────┬─────┘             └────────┬──────────┘
         │                            │
         └─────────────┬──────────────┘
                       ▼
              ┌────────────────┐
              │  Story Writer  │
              │   (Agent 3)    │
              │  Draft stories │
              │  topics+rules  │
              └───────┬────────┘
                      │
                      ▼
              ┌────────────────┐
              │ Epic Decomposer│
              │   (Agent 4)    │
              │ Group epics,   │
              │ break into     │
              │    tasks       │
              └───────┬────────┘
                      │
                      ▼
              ┌────────────────┐
              │  Gap Detector  │
              │   (Agent 5)    │
              │ Duplicates     │
              │ (embeddings),  │
              │ conflicts, gaps│
              └───────┬────────┘
                      │
                      ▼
              ┌────────────────┐
              │   finalize     │
              │  Guardrails,   │
              │  audit trail   │
              └───────┬────────┘
                      │
                     END
```

Agents 1 and 2 run **in parallel** — Agent 2 does not depend on Agent 1's output. Both must complete before Agent 3 starts. This saves ~40% wall time on Premium preset runs.

### 2.2 Where AI sits, where deterministic Python sits

| Concern | Technology | Location |
|---------|-----------|----------|
| Fetch Jira tickets via REST | Deterministic (requests) | `src/tools/jira_tool.py` |
| Fetch Confluence page | Deterministic (requests) | `src/tools/confluence_tool.py` |
| Parse uploaded PDF/TXT/MD | Deterministic | `src/tools/file_parser.py` |
| Scan input for prompt injection | Regex (8 pattern categories) | `src/security.py` `InputSanitizer` |
| PII detection & redaction | Regex (4 PII pattern types) | `src/security.py` |
| Extract topics from transcript | **Claude / Gemini API call** | Agent 1 — ParserAgent |
| Extract constraints from wiki | **Claude / Gemini API call** | Agent 2 — ConstraintAgent |
| Draft user stories | **Claude / Gemini API call** | Agent 3 — StoryWriterAgent |
| Group into epics, add tasks | **Claude / Gemini API call** | Agent 4 — EpicDecomposerAgent |
| Embed tickets for duplicate search | Local sentence-transformers | `src/tools/embedding_tool.py` |
| Find semantic duplicate candidates | Cosine similarity (numpy) | `src/tools/embedding_tool.py` |
| Judge duplicates, conflicts, gaps | **Claude / Gemini API call** | Agent 5 — GapDetectorAgent |
| Scan output for PII/toxicity | Regex + keyword rules | `src/security.py` `OutputScanner` |
| Apply quality guardrails | Deterministic rules | `src/guardrails.py` |
| Render output tabs | Deterministic | `app.py` |
| Create Jira Epic/Story/Sub-task | Deterministic (REST) | `src/tools/jira_tool.py` |
| OAuth2 / SSO | Deterministic | `src/entra_auth.py` |

**The LLM is called at exactly five points per pipeline run.** Everything else is deterministic Python.

### 2.3 Technology stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Language | Python 3.11 | Ecosystem, LangChain/LangGraph native |
| Pipeline | Custom `Orchestrator` class (`src/orchestrator.py`) + `pipeline.py` | Sequential stage management, graceful degradation |
| Primary LLM | Anthropic Claude (claude-sonnet-4-5) | Strong instruction-following, reliable JSON, prompt caching |
| Secondary LLM | Google Gemini 2.5 Flash (`gemini-2.5-flash`) | Fast, cost-efficient, good for mechanical extraction stages |
| Local LLM | Ollama (`llama3.2:3b`) | Zero API cost for development |
| LLM framework | LangChain (`langchain-anthropic`, `langchain-google-genai`) | Unified interface, retry logic |
| Web UI | Streamlit (port 8501) | Python-native, no separate frontend repo |
| Duplicate detection | `sentence-transformers` (`all-MiniLM-L6-v2`) | Local 384-d embeddings, no extra API calls |
| Vector store | In-process numpy / NPZ file cache / ChromaDB | Three configurable backends |
| Backlog integration | Custom `requests`-based Jira/Confluence client | Full control, MCP server support |
| Auth | Microsoft Entra ID OAuth2 via `PyJWT` + JWKS | Enterprise SSO |
| Circuit breakers | Custom `CircuitBreaker` class | Per-provider fault isolation |
| Container | Docker (`python:3.11-slim-bookworm`) | Reproducible |
| Registry | Azure Container Registry (ACR) | Private image storage |
| Hosting | Azure Container Apps | Serverless, scale-to-zero, managed HTTPS |
| Secrets | Azure Key Vault | All credentials, never in environment directly |
| Storage | Azure Files shares | Persistent `logs/` and `outputs/` across replicas |
| Redis | Azure Cache for Redis (Basic C0) | Cross-pod budget/rate limit enforcement |
| IaC | Terraform (`hashicorp/azurerm ~> 3.100`) | All Azure resources declared as code |
| CI/CD | GitHub Actions | Lint → test → build → deploy pipeline |
| Metrics | Prometheus endpoint (port 9090) | `backlog_*` metric family |
| Tracing | OpenTelemetry OTLP → Grafana Cloud Tempo | Per-stage spans |

---

## Part 3 — Five Specialist Agents

### 3.1 Agent 1 — ParserAgent

**File:** `src/agents/parser_agent.py`  
**Prompt:** `prompts/parser_prompt.md`

**Job:** Read raw transcript text (and optional whiteboard images) and extract distinct, high-confidence topics.

**Constructor:**
```python
def __init__(
    self,
    claude: Tool | None = None,
    memory: MemoryStore | None = None,
    audit: AuditLog | None = None,
    *,
    tool: Tool | None = None,
) -> None
```

**Run signature:**
```python
def run(
    self,
    transcript_text: str,
    vision_attachments: list | None = None,
) -> None
```

**What the Anthropic API call looks like on the wire (vision mode):**
```
POST https://api.anthropic.com/v1/messages
anthropic-version: 2023-06-01
x-api-key: sk-ant-api03-...

{
  "model": "claude-sonnet-4-5",
  "max_tokens": 4000,
  "system": [{"type": "text", "text": "<system prompt>", "cache_control": {"type": "ephemeral"}}],
  "messages": [{
    "role": "user",
    "content": [
      {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}},
      {"type": "text", "text": "<parser prompt with {{TRANSCRIPT}} substituted>"}
    ]
  }]
}
```

Images are sent **before** the text block, as Anthropic recommends for best attention.

**Output written to memory:**
```json
{
  "summary": "Two-to-four sentence description of what the meeting covered.",
  "topics": [
    {
      "id": "T-01",
      "theme": "offline-payment-support",
      "summary": "Customers want to complete purchases without internet connectivity",
      "raw_quote": "We keep losing sales at pop-up events because there's no signal",
      "speaker": "Sarah (Product)",
      "sentiment": "concern"
    }
  ]
}
```

**Key prompt decisions:**
- `theme` must be lowercase-hyphenated — produces stable, machine-readable IDs
- `sentiment` must be one of `concern | request | observation | praise` — prevents free-text drift
- "Prefer fewer, higher-confidence topics" — reduces false-positive story generation downstream
- Blocked requests are **kept**, not filtered — they surface as constraint conflicts in Agent 5, which is more useful than silent removal

**Stage skip:** If no transcript text and no vision attachments, the stage is skipped with a `stage_skipped` audit event.

---

### 3.2 Agent 2 — ConstraintAgent

**File:** `src/agents/constraint_agent.py`  
**Prompt:** `prompts/constraint_extractor_prompt.md`

**Job:** Read architectural wiki text and extract binding rules — musts, shoulds, and forbiddens.

**Run signature:**
```python
def run(self, wiki_text: str) -> None
```

**Live Confluence fetch (when `live_confluence_page_id` is set):**
```
GET https://<tenant>.atlassian.net/wiki/api/v2/pages/{page_id}
Authorization: Basic base64(email:CONFLUENCE_API_TOKEN)
```

The response HTML is stripped to plain text via `_strip_confluence_storage_format()` before being passed to the LLM — removing XHTML tags reduces token count by ~40%.

**Output written to memory:**
```json
{
  "constraints": [
    {
      "id": "C-01",
      "severity": "must",
      "category": "offline",
      "statement": "All POS payment operations must support offline queuing with AES-256 local storage",
      "source_excerpt": "Section 3.2: The platform must operate in degraded mode...",
      "applies_to": ["pos"]
    },
    {
      "id": "C-02",
      "severity": "forbidden",
      "category": "security",
      "statement": "PII must not be stored in browser localStorage",
      "source_excerpt": "Security Policy 7.1: Client-side PII persistence is prohibited...",
      "applies_to": ["*"]
    }
  ]
}
```

**Severity mapping:**
- `must` — story MUST implement this or it is invalid
- `should` — story SHOULD implement this; absence is a gap, not a blocker
- `forbidden` — story explicitly cannot do this; doing so is a conflict

**Category taxonomy:** `integration | performance | security | compliance | platform | data | offline | other`

**Stage skip:** If no wiki text and no `live_confluence_page_id`, skipped with audit event. Downstream agents receive an empty constraints list and continue normally.

---

### 3.3 Agent 3 — StoryWriterAgent

**File:** `src/agents/story_writer_agent.py`  
**Prompt:** `prompts/story_writer_prompt.md`

**Job:** Draft user stories from topics and constraints. This is the highest-stakes agent — its output is what users see and approve. It uses the most capable model in every preset (always Claude Sonnet in Balanced and Premium).

**Run signature:**
```python
def run(self) -> None  # reads topics + constraints from MemoryStore
```

**Output written to memory:**
```json
{
  "stories": [
    {
      "id": "ST-01",
      "title": "Offline card payment queuing",
      "description": "Enable customers to complete card payments when connectivity is unavailable, syncing when back online",
      "user_story": "As a field sales associate, I want to accept card payments without internet so that I never lose a sale at remote pop-up events",
      "acceptance_criteria": [
        "Given the device is offline, when a customer taps to pay, then the payment is queued locally with AES-256 encryption",
        "Given the device regains connectivity, when the queue is non-empty, then queued payments sync automatically within 30 seconds",
        "Given a queued payment fails to sync after 3 retries, then the customer receives a written receipt and the associate is alerted"
      ],
      "priority": "High",
      "priority_rationale": "Lost sales at events is a direct revenue impact. Multiple speakers confirmed this is a frequent occurrence.",
      "tags": ["pos", "offline-mode", "payments", "compliance"],
      "source_topic_id": "T-01",
      "evidence": [
        {
          "topic_id": "T-01",
          "theme": "offline-payment-support",
          "raw_quote": "We keep losing sales at pop-up events because there's no signal",
          "speaker": "Sarah (Product)",
          "sentiment": "concern"
        }
      ],
      "potential_constraint_conflicts": ["C-02"]
    }
  ]
}
```

**Key prompt rules:**
1. `acceptance_criteria` must use Given/When/Then format — testable by a QA engineer without interpretation
2. `priority_rationale` must reference specific evidence from the transcript — no hallucinated justifications
3. The `evidence` block is populated from the matching topic's `raw_quote` and `speaker` — every story is traceable back to a human voice in the meeting
4. Constraint list is injected but the Story Writer does **not** enforce constraints — that is Gap Detector's job. This separation keeps the Story Writer focused on writing, not policing
5. Blocked requests are drafted and flagged, never suppressed: "Never suppress a blocked story; draft it and flag conflict"

**Auto-repair logic (deterministic Python, not LLM):**
After the LLM responds, the agent validates that each story's `source_topic_id` references a real topic. Invalid values (e.g. `"..."` or `"T-99"`) are corrected via word-overlap scoring between story text and topic summaries. Repairs are logged as `story_repair` audit events.

**Stage skip:** If Agent 1 produced zero topics, skipped with audit event.

---

### 3.4 Agent 4 — EpicDecomposerAgent

**File:** `src/agents/epic_decomposer_agent.py`  
**Prompt:** `prompts/epic_decomposer_prompt.md`

**Job:** Group related stories into epics; decompose each story into typed implementation tasks.

**Run signature:**
```python
def run(self) -> None  # reads stories from MemoryStore, writes epics
```

**Output written to memory:**
```json
{
  "epics": [
    {
      "id": "EP-01",
      "title": "Offline-First Retail Experience",
      "description": "All capabilities needed for store associates to operate without internet connectivity",
      "stories": [
        {
          "id": "ST-01",
          "title": "Offline card payment queuing",
          "user_story": "...",
          "acceptance_criteria": ["..."],
          "priority": "High",
          "priority_rationale": "...",
          "tags": ["pos", "offline-mode"],
          "source_topic_id": "T-01",
          "evidence": [...],
          "tasks": [
            {
              "id": "ST-01-TK-01",
              "title": "Implement AES-256 local payment queue with persistence",
              "type": "backend"
            },
            {
              "id": "ST-01-TK-02",
              "title": "Design WAN-outage detection and offline mode UI",
              "type": "frontend"
            },
            {
              "id": "ST-01-TK-03",
              "title": "Spike: PCI-compliant offline card architectures",
              "type": "spike"
            },
            {
              "id": "ST-01-TK-04",
              "title": "Integration tests for offline→online sync",
              "type": "qa"
            }
          ]
        }
      ]
    }
  ]
}
```

**Task type taxonomy:** `backend | frontend | data | infra | qa | spike`

**Preservation rule:** The prompt instructs the model to copy every input story field verbatim and only add the `tasks` array — no rewriting of story content.

**Target:** 3–7 tasks per story.

**Stage skip:** If Agent 3 produced zero stories, skipped with audit event.

---

### 3.5 Agent 5 — GapDetectorAgent

**File:** `src/agents/gap_detector_agent.py`  
**Prompt:** `prompts/gap_detector_prompt.md`

**Job:** Find semantic duplicates against the existing backlog, identify constraint violations, and surface implied requirements that have no story yet.

**Constructor:**
```python
def __init__(
    self,
    claude: Tool | None = None,
    jira: JiraTool | None = None,
    github: GithubTool | None = None,
    memory: MemoryStore | None = None,
    audit: AuditLog | None = None,
    *,
    tool: Tool | None = None,
    use_embeddings_for_duplicates: bool = True,
    embedding_tool: EmbeddingTool | None = None,
    duplicate_threshold: float = 0.6,
) -> None
```

**This agent uses a hybrid approach for duplicates:**

**Step 1 — Local embedding-based candidate retrieval (no LLM call):**
```python
# EmbeddingTool.find_duplicates() — src/tools/embedding_tool.py
model = SentenceTransformer("all-MiniLM-L6-v2")

# Embed all existing Jira tickets
existing_embeddings = model.encode([t["summary"] for t in existing_tickets])

# For each new story, find top-5 candidates above threshold
for story in new_stories:
    story_embedding = model.encode([story["title"] + ". " + story["description"]])[0]
    similarities = cosine_similarity([story_embedding], existing_embeddings)[0]
    
    candidates = [
        {"existing_id": existing_tickets[i]["id"], "score": float(similarities[i])}
        for i in range(len(existing_tickets))
        if similarities[i] >= threshold  # default 0.6
    ]
    top_k_candidates = sorted(candidates, key=lambda x: x["score"], reverse=True)[:5]
```

**Confidence thresholds:**
- `high` — similarity ≥ 0.85
- `medium` — similarity ≥ 0.78
- `low` — below 0.78

**Step 2 — LLM judgment on conflicts and gaps:**

The LLM prompt receives:
- Full stories list
- Constraints list
- Only the top-5 candidates per story (not the full backlog) for token efficiency

The model is asked to:
1. For each story with candidates: decide if it is truly a duplicate (with confidence and reason)
2. For each story: check if it violates any `must` or `forbidden` constraint
3. Identify gaps — requirements clearly implied by the inputs that no story covers

**Output written to memory:**
```json
{
  "duplicates": [
    {
      "story_id": "ST-01",
      "existing_id": "NS-347",
      "confidence": "high",
      "reason": "ST-01 and NS-347 both address offline payment queuing. NS-347 is iOS-only; ST-01 is cross-platform, but the core capability is identical.",
      "similarity": 0.91
    }
  ],
  "conflicts": [
    {
      "story_id": "ST-03",
      "with": "C-02",
      "severity": "high",
      "reason": "ST-03 proposes storing card tokens in browser localStorage, which is explicitly forbidden by Security Policy 7.1."
    }
  ],
  "gaps": [
    {
      "id": "G-01",
      "title": "Network timeout and retry UX for offline payment sync",
      "description": "The offline payment flow implies graceful degradation on flaky connections, but no story addresses partial connectivity or retry behavior.",
      "related_ids": ["ST-01"],
      "evidence": "Implied by the offline queue sync requirement — sync must handle timeouts, but no story specifies this behavior."
    }
  ]
}
```

**Why local embeddings instead of pure LLM for duplicates?**

Sending 200 Jira tickets to the LLM for every story comparison would cost significantly more per run and take 3× longer. `sentence-transformers` runs in milliseconds with zero API cost. The LLM is only needed for the final judgment: "given these top-5 candidates, is this actually a duplicate?" This embed → narrow → judge pattern is the same RAG approach used in production retrieval systems.

---

## Part 4 — Pipeline Orchestration

### 4.1 Orchestrator class

**File:** `src/orchestrator.py` (1,244 lines)

The `Orchestrator` is a stateless class — instantiated fresh per run. It owns the `MemoryStore`, `AuditLog`, and all tool instances. It calls each agent in order, passing the shared `MemoryStore` for inter-agent communication.

**Full `run()` signature:**
```python
def run(
    self,
    transcript_text: str = "",
    constraint_text: str = "",
    existing_tickets: list[dict] | None = None,
    redact_pii: bool = False,
    strict_redact: bool = False,
    progress_callback=None,
    dry_run: bool = False,
    models: dict[str, str] | None = None,
    use_embeddings_for_duplicates: bool = True,
    persistent_memory: bool | None = None,
    live_confluence_page_id: str | None = None,
    live_jira: bool = False,
    vision_attachments: list | None = None,
    auto_switch: bool = True,
    run_metadata: dict | None = None,
) -> dict[str, Any]
```

**Return value:**
```python
{
    "summary": str,
    "topics": list[dict],
    "constraints": list[dict],
    "epics": list[dict],          # nested: epic → stories → tasks
    "gaps": list[dict],
    "conflicts": list[dict],
    "duplicates": list[dict],
    "guardrail_findings": list[dict],
    "token_usage": {
        "<stage_name>": {"input": N, "output": N},
        "total": {"input": N, "output": N}
    },
    "model": str,                 # human-readable summary
    "models": dict[str, str],     # per-stage assignments
    "audit_trail": str,           # rendered Markdown
    "audit_chain_fingerprint": str,
    "run_metadata": dict,
}
```

### 4.2 MemoryStore as the inter-agent bus

All agents share a single `MemoryStore` instance. Each agent reads from keys written by earlier stages and writes its own keys. No agent can see what another agent is writing — they read only from known, documented keys.

```python
memory = MemoryStore(persistent=persistent_memory)
audit  = AuditLog()
inc_active_synthesis()   # increments backlog_active_synthesis metric

# Agent 1
parser = ParserAgent(tool=parser_tool, memory=memory, audit=audit)
with stage_span("parser", model=resolved_models["parser"]):
    parser.run(transcript_text, vision_attachments=vision_attachments)

# Agent 2 (runs concurrently with Agent 1 in pipeline.py)
constraint = ConstraintAgent(tool=constraint_tool, memory=memory, audit=audit)
with stage_span("constraint_extractor", model=resolved_models["constraint"]):
    constraint.run(constraint_text)

# Agent 3 — reads topics + constraints from memory
story_writer = StoryWriterAgent(tool=story_tool, memory=memory, audit=audit)
with stage_span("story_writer", model=resolved_models["story_writer"]):
    story_writer.run()

# ... and so on for Agents 4 and 5

dec_active_synthesis()   # decrements backlog_active_synthesis metric
```

### 4.3 Graceful degradation

Each agent call is wrapped in a try/except. Earlier stage outputs are never discarded.

| Failed stage | User sees |
|---|---|
| `parser` | Zero topics. Downstream agents receive empty topics. Warning banner. |
| `extract_constraints` | Empty constraints. Story Writer and Gap Detector run without constraint context. Warning. |
| `write_stories` | Zero stories. Epic Decomposer and Gap Detector skipped. Warning. |
| `decompose_epics` | Stories shown flat without epic grouping. Warning. |
| `detect_gaps` | No conflicts/duplicates/gaps shown. Warning. |

**Principle: never lose work from earlier stages.** If three agents succeeded and two failed, the user gets partial output rather than a blank screen.

### 4.4 Model failover (auto_switch)

When `auto_switch=True` (default), each stage attempts a provider failover cascade if the primary model fails:

```
Claude (Sonnet) → Gemini 2.5 Flash → Ollama llama3.2:3b (if running) → Claude (last resort)
Gemini 2.5 Flash → Ollama (if running) → Claude Sonnet (cloud fallback)
Ollama → Claude Sonnet (cloud fallback)
```

The failover is governed by per-provider `CircuitBreaker` instances. Each breaker trips to `OPEN` after 3 consecutive failures (`CB_FAILURE_THRESHOLD=3`) and enters `HALF_OPEN` after a recovery timeout to probe if the provider is healthy again.

### 4.5 PII redaction

When `redact_pii=True`:
1. The `Redactor` processes `transcript_text`, `constraint_text`, and ticket descriptions before any LLM call
2. A stable placeholder map is maintained (`<EMAIL_1>`, `<PHONE_1>`, etc.) — the same value always maps to the same token across all inputs
3. The final synthesis output is **un-redacted** on the way out, so users see normal output
4. The audit log records prompts in the **redacted** form — a CISO can audit the log without seeing PII

`strict_redact=True` halts the pipeline with an error if PII is still detected after the redaction pass.

---

## Part 5 — AI Tools Layer

### 5.1 ClaudeTool

**File:** `src/tools/claude_tool.py`

Wrapper around `langchain_anthropic.ChatAnthropic`. Provides a consistent `call() / call_for_json()` interface shared by all agents.

**Interface:**
```python
def call(
    self,
    user_message: str,
    max_tokens: int = 4000,
    *,
    images: list[VisionAttachment] | None = None,
) -> tuple[str, dict[str, Any]]
# Returns: (response_text, {"input_tokens": N, "output_tokens": N})

def call_for_json(
    self,
    user_message: str,
    max_tokens: int = 4000,
    *,
    images: list[VisionAttachment] | None = None,
) -> tuple[dict, dict[str, Any]]
# Returns: (parsed_dict, {"input_tokens": N, "output_tokens": N})
```

**Prompt caching:**

When the system prompt is ≥ 1,024 characters (on Claude 3.5+), it is sent with `cache_control: {"type": "ephemeral"}`. Anthropic's prompt caching reduces input token costs for cached content to ~10% of uncached cost, with a 5-minute TTL. For five API calls per run, if the system prompt is 2,000 tokens, caching saves ~80-90% of those tokens on calls 2–5.

```python
if len(system_prompt) >= 1024:
    system_content = [
        {"type": "text", "text": system_prompt,
         "cache_control": {"type": "ephemeral"}}
    ]
else:
    system_content = system_prompt
```

**Retry logic:** `tenacity` with exponential backoff (1–10s) on `RateLimitError` / `APIConnectionError`. Max retries controlled by `AGENT_MAX_RETRIES` env var (default 3).

**JSON extraction (defensive):**
```python
def _extract_json_block(text: str) -> dict:
    # Try 1: markdown code fence
    if m := re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
        return json.loads(m.group(1))
    # Try 2: first { to last }
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1:
        return json.loads(text[start:end+1])
    raise AgentError(f"No JSON block found in response. First 300 chars: {text[:300]}")
```

**Vision support:**
```python
if images:
    content = [
        {"type": "image",
         "source": {"type": "base64", "media_type": img.media_type, "data": img.data_b64}}
        for img in images
    ] + [{"type": "text", "text": user_message}]
else:
    content = user_message
```

**Environment:** `ANTHROPIC_API_KEY` (required), `ANTHROPIC_MODEL` (default `claude-sonnet-4-5`)

### 5.2 GeminiTool

**File:** `src/tools/gemini_tool.py`

Drop-in replacement using the new `google-genai` SDK (not the deprecated `google-generativeai`).

**Same `call() / call_for_json()` interface** — agents never know which provider they're using.

**Error classification:**
- **Transient** (retry): quota, rate, 429, resource_exhausted, deadline_exceeded, unavailable, 503, 502, 500, timeout, connection errors
- **Permanent** (fail fast): auth errors, invalid requests

**Differences from ClaudeTool:**
- No vision support in the current wrapper
- No prompt caching (Gemini charges by token regardless)

**Environment:** `GOOGLE_API_KEY` or `GEMINI_API_KEY`, `GEMINI_MODEL` (default `gemini-2.5-flash`)

### 5.3 OllamaTool

**File:** `src/tools/ollama_tool.py`

Local LLM via Ollama. Accepts model IDs prefixed with `ollama/` (e.g. `ollama/llama3.2:3b`).

**Health check:** `GET {OLLAMA_BASE_URL}/api/tags` with a 2-second timeout. If Ollama is not running, the tool reports unavailable and the circuit breaker routes to the next provider.

**Use case:** Zero-cost local development. Story quality with `llama3.2:3b` is noticeably lower than Sonnet but sufficient for validating pipeline structure and UI changes without spending API budget.

### 5.4 Model presets

Four presets are defined in `app.py` as `MODEL_PRESETS`:

| Preset | parser | constraint | story_writer | epic_decomposer | gap_detector |
|--------|--------|------------|--------------|-----------------|--------------|
| **local** | `ollama/llama3.2:3b` | `ollama/llama3.2:3b` | `claude-sonnet-4-5` | `ollama/llama3.2:3b` | `claude-sonnet-4-5` |
| **free** | `gemini-2.5-flash` | `gemini-2.5-flash` | `gemini-2.5-flash` | `gemini-2.5-flash` | `gemini-2.5-flash` |
| **balanced** | `gemini-2.5-flash` | `gemini-2.5-flash` | `claude-sonnet-4-5` | `gemini-2.5-flash` | `claude-sonnet-4-5` |
| **premium** | `claude-sonnet-4-5` | `claude-sonnet-4-5` | `claude-sonnet-4-5` | `claude-sonnet-4-5` | `claude-sonnet-4-5` |

**Cost bands:**
- `local` — ~$0 (Ollama must be running locally)
- `free` — ~$0 (Gemini free tier)
- `balanced` — ~$0.01 per run (2× Claude stages: Story Writer + Gap Detector)
- `premium` — ~$0.03 per run (all 5 stages on Claude Sonnet)

**Design rationale:** Parse and constraint extraction are mechanical (structured extraction from text). Story writing and gap detection require richer reasoning — story ACs must be testable, conflict/gap analysis must be nuanced. The Balanced preset puts Sonnet only where reasoning complexity justifies the cost.

**Per-stage override:** Advanced users can override individual stages from the sidebar. Custom mappings persist to `.ui_state_<HOSTNAME>.json`.

### 5.5 Circuit breakers

**File:** `src/circuit_breaker.py`

One `CircuitBreaker` instance per LLM provider, shared across the process.

**States:**
- `CLOSED` (0) — normal operation; all calls go through
- `OPEN` (1) — fast-failing; calls are immediately redirected to the next provider
- `HALF_OPEN` (2) — one probe call allowed to test recovery; success → CLOSED, failure → OPEN with refreshed timeout

**Configuration:**
- `CB_FAILURE_THRESHOLD` — failures before tripping to OPEN (default 3)
- `CB_RECOVERY_TIMEOUT` — seconds in OPEN before trying HALF_OPEN

State transitions are exported to Prometheus via `backlog_circuit_breaker_state{provider}` gauge.

---

## Part 6 — Jira & Confluence Integration

### 6.1 JiraTool — two operating modes

**File:** `src/tools/jira_tool.py`

**Mock mode** (`JIRA_MODE=mock`, default): Reads fixture data from `samples/jira_backlog.json`. Contains plausible NorthStar Retail tickets so duplicate detection has real results in demos.

**Live mode** (all of `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` set): Hits Jira Cloud REST `/rest/api/3/search/jql`.

```python
def __init__(
    self,
    fixture_path: Path | None = None,
    *,
    mode: Mode | None = None,
    base_url: str | None = None,
    email: str | None = None,
    api_token: str | None = None,
    project_key: str | None = None,
    page_size: int = 50,
    max_results: int = 200,
)
```

**Key methods:**
- `list_all()` — all visible tickets (response cached per instance)
- `search(query)` — substring search (mock) or JQL (live)
- `create_issue(summary, description_adf, issue_type, labels, parent_key, project_key)` — defensive retry: drops parent → drops labels → drops issue type on each `400` response

**Response normalization:** Reshapes the Atlassian API response to a consistent `{id, title, description, status, labels, raw}` shape regardless of field names in the source.

### 6.2 Jira ticket creation (Publish flow)

When the user clicks **Publish to Jira**, the tool creates a full Epic → Story → Sub-task hierarchy:

```python
def publish_synthesis(result: dict, project_key: str) -> dict:
    created = {"epics": [], "stories": [], "tasks": []}

    for epic in result["epics"]:
        epic_issue = create_issue({
            "project": {"key": project_key},
            "summary": epic["title"],
            "description": epic["description"],
            "issuetype": {"name": "Epic"},
        })
        for story in epic["stories"]:
            story_issue = create_issue({
                "project": {"key": project_key},
                "summary": story["title"],
                "description": _format_story_description(story),  # wiki markup
                "issuetype": {"name": "Story"},
                "priority": {"name": story["priority"]},
                "parent": {"key": epic_issue["key"]},
                "labels": story.get("tags", []),
            })
            for task in story.get("tasks", []):
                create_issue({
                    "project": {"key": project_key},
                    "summary": task["title"],
                    "issuetype": {"name": "Sub-task"},
                    "parent": {"key": story_issue["key"]},
                })
    return created
```

`_format_story_description()` converts acceptance criteria from a Python list into Jira Wiki Markup with `h3.` headings for User Story, Acceptance Criteria, Evidence, and AI Metadata sections.

### 6.3 ConfluenceTool

**File:** `src/tools/confluence_tool.py`

**Live mode:** Hits Confluence Cloud REST `/wiki/api/v2/pages/{id}` (v2 API).

**Format conversion:**
- `_strip_confluence_storage_format()` — strips XHTML tags, decodes HTML entities, collapses whitespace to plain text
- `markdown_to_confluence_storage(md)` — converts a small Markdown dialect (headings, lists, fenced code, inline emphasis) to Confluence XHTML storage format — used when creating new pages

**Additional live-only methods:**
- `list_spaces(limit=25)` — returns `[{id, key, name, type}]`
- `create_page(space_id, title, body_storage, parent_id)` — creates a new page and returns links

### 6.4 MCP server integration

When `ATLASSIAN_MCP_ENABLED=1`, the orchestrator instantiates `MCPJiraTool` and `MCPConfluenceTool` instead of the REST tools. These delegate to the `mcp-atlassian` server process. The transport layer (MCP vs REST vs fixture) is recorded in the audit trail's `pipeline_started` event for full data-source provenance.

Similarly, `GITHUB_MCP_ENABLED=1` enables `MCPGithubTool` for GitHub Issues integration.

---

## Part 7 — Security Layer

### 7.1 InputSanitizer — injection scanning

**File:** `src/security.py`

All user-supplied text (transcript, constraints, ticket descriptions) is scanned **before any LLM sees it**. Eight injection pattern categories:

```python
_INJECTION_PATTERNS = [
    # 1. Instruction override
    r"\bignore\s+(all\s+)?(previous|prior|above|your)\s+(instructions?|rules?|constraints?)",

    # 2. Role hijacking
    r"\b(you\s+are\s+now|act\s+as|pretend\s+to\s+be|roleplay\s+as)\b",

    # 3. System prompt extraction
    r"\b(reveal|show|print|output|display)\s+(your\s+)?(system\s+)?prompt",

    # 4. LLM special tokens
    r"<\|endoftext\|>|\[INST\]|\[/INST\]|<<SYS>>|<\|im_start\|>|<\|im_end\|>",

    # 5. Chat-role prefix injection
    r"^(SYSTEM|USER|ASSISTANT)\s*:",

    # 6. Jailbreak keywords
    r"\b(DAN\s+mode|developer\s+mode|god\s+mode|jailbreak|bypass\s+safety|no\s+restrictions)\b",

    # 7. Data exfiltration
    r"\b(send|POST|exfiltrate|transmit)\s+.{0,50}\bto\b.{0,100}@",

    # 8. Verbatim repeat
    r"\brepeat\s+(everything|all|verbatim|word\s+for\s+word|exactly)",
]
```

Matched injections are **replaced** with `[INJECTION REDACTED]`, not blocked. The pipeline continues with sanitized input. Findings appear in the Guardrails tab.

**Why replace rather than block?** A meeting transcript might legitimately contain someone saying "ignore the previous plan" — that is domain content, not an attack. Replacement preserves context while neutralizing the injection vector.

### 7.2 PII detection

Four PII pattern types detected in both input scanning and output scanning:

| Pattern | Regex |
|---------|-------|
| Email | `[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}` |
| Phone (US) | `+?1[\s.-]?\(?[2-9]\d{2}\)?[\s.-]?\d{3}[\s.-]?\d{4}` |
| SSN | `\d{3}[-\s]\d{2}[-\s]\d{4}` |
| Card numbers | `(?:\d[ -]?){13,19}\d` |

**Strict redact mode:** `strict_redact=True` halts the pipeline and records a `strict_redact_violation` audit event if PII is still present after the redaction pass.

### 7.3 Quality guardrails

**File:** `src/guardrails.py`

Post-synthesis quality rules (deterministic Python — no LLM):

| Rule | Trigger | Severity |
|------|---------|---------|
| Acceptance criteria count | < 2 or > 7 ACs per story | `warn` |
| AC grammar | Not all ACs use Given/When/Then | `warn` |
| Duplicate story titles | Two stories with identical titles | `error` |
| Non-canonical tags | Tags not in the approved taxonomy | `warn` |
| Story grounding | `source_topic_id` doesn't match any topic in memory | `warn` |
| Empty priority rationale | `priority_rationale` is blank | `warn` |

**`GuardrailFinding` dataclass:**
```python
@dataclass
class GuardrailFinding:
    code: str         # e.g. "ac_count_too_low"
    severity: str     # "error | warn | info"
    message: str
    story_id: str | None = None

    def to_dict(self) -> dict
```

**Canonical tag taxonomy:**
```
pos, mobile-app, ecommerce, loyalty, inventory, pharmacy,
vendor-portal, store-associate, analytics, payments,
offline-mode, accessibility, performance, security, compliance
```

---

## Part 8 — Memory & Audit Trail

### 8.1 MemoryStore

**File:** `src/memory/store.py`

Two interfaces on a single class:

**KV store** — explicit key/value handoff between agents:
```python
store.put("topics", topics_list)       # After Agent 1
store.put("constraints", constraints)  # After Agent 2
topics = store.get("topics")          # In Agent 3
```
Backed by a plain `dict` — values live in the Python process. Each synthesis run gets a fresh `MemoryStore` instance — no state shared between runs.

**Vector store** — semantic search for duplicate detection:
```python
store.index_tickets(existing_tickets)        # Called at pipeline start
candidates = store.search_similar(
    query="offline payment queuing",
    top_k=5
)
```

**Three configurable backends:**

| Backend | Activation | Persistence | Notes |
|---------|-----------|-------------|-------|
| In-process numpy (default) | Always | None — lost on restart | Sufficient for single-process |
| NPZ file cache | `MEMORY_PERSISTENT=1` | `.cache/memory/<hash>.npz` | Survives restarts, same corpus |
| ChromaDB | `USE_CHROMADB=1` | `.cache/memory/chroma/` | Multi-replica shared state |

**Embedding model:** `sentence-transformers/all-MiniLM-L6-v2` (384-d vectors, L2-normalized)

**Small-corpus optimisation:** If `existing_tickets` has fewer than 20 items, embeddings are skipped entirely and all items are returned as candidates for the LLM to judge directly.

### 8.2 AuditLog

**File:** `src/memory/audit_log.py`

Every significant event in the pipeline is appended to the audit log. Two persistence layers run in parallel:
1. **In-memory list** — for the current run's Markdown rendering
2. **SQLite database** (`logs/audit_chain.db`) — persistent, tamper-evident, append-only

**SQLite schema:**
```sql
CREATE TABLE audit_events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT,
    timestamp    TEXT,
    agent        TEXT,
    event        TEXT,
    payload_json TEXT,
    reasoning    TEXT,
    prev_hash    TEXT,
    event_hash   TEXT
)
```

**Chain fingerprinting:**
```python
def _update_fingerprint(self, event: AuditEvent, prev_hash: str) -> str:
    event_bytes = json.dumps(
        {"agent": event.agent, "event": event.event, "payload": event.payload},
        sort_keys=True
    ).encode()
    combined = (prev_hash + event_bytes.hex()).encode()
    return hashlib.sha256(combined).hexdigest()
```

Tampering with any audit event breaks the chain — `verify_chain()` recomputes hashes from the stored rows and compares against stored `event_hash` values.

**Key methods:**
- `record(agent, event, payload, reasoning)` — append event + persist to SQLite
- `record_tool_call(agent, tool, request, response_excerpt, tokens_used, usage, prompt, response_text)` — captures full prompts/responses (capped at 16 KB each) for the audit trail
- `record_failure(agent, error)` — records a terminal failure with reasoning
- `render_markdown()` — produces collapsible Markdown with prompts/responses in `<details>` blocks
- `verify_chain()` — re-derives hash chain from SQLite rows, returns `True` if intact

**Audit trail Markdown format:**
```markdown
## Audit Trail — run_abc123

**Started:** 2026-06-15T10:00:00Z  
**User:** mani.kantan.arun@northstarretail.com  
**Model preset:** balanced  
**Chain fingerprint:** a3b4c5d6...

<details>
<summary>🔍 stage: parser | event: tool_call | 2026-06-15T10:00:02Z</summary>

**Model:** claude-sonnet-4-5  
**Input tokens:** 2,847  
**Output tokens:** 1,203  

**Prompt:**
[full prompt text]

**Response (excerpt):**
{"topics": [{"id": "T-01", "theme": ...
</details>
```

---

## Part 9 — Microsoft Entra SSO

### 9.1 Why Entra ID

Enterprise deployments require:
- Single Sign-On with existing corporate Microsoft 365 credentials
- MFA enforced by the enterprise's existing conditional access policies
- Per-user audit logging of who accessed the synthesis tool and when
- App role assignment — only specific AD groups can run synthesis (`admin`, `contributor`, `viewer`)

### 9.2 App Registration configuration

In Azure Portal → Entra ID → App Registrations:

| Field | Notes |
|-------|-------|
| Application (client) ID | Stored as `ENTRA_CLIENT_ID` env var |
| Directory (tenant) ID | Stored as `ENTRA_TENANT_ID` env var |
| Tenant domain | Stored as `ENTRA_TENANT_DOMAIN` (e.g. `tenant.onmicrosoft.com`) |
| Redirect URI | `ENTRA_REDIRECT_URI` (defaults to `http://localhost:8501/`) |
| Supported account types | Single tenant |
| Client secret | Stored as `ENTRA_CLIENT_SECRET` in Azure Key Vault |

**Critical setup:** In Enterprise Applications → Users and Groups, users or groups must be explicitly assigned. Without this, users see `AADSTS50105: Your administrator has configured the application to block users unless they are specifically granted access`. This is expected behavior when `Assignment required = Yes`.

### 9.3 Complete OAuth2 flow

```
1. User clicks "Sign in with Microsoft"

2. App calls generate_state_nonce() → src/entra_auth.py
   - secrets.token_urlsafe(32) → 32-byte random nonce
   - Timestamp appended: payload = "<random>.<unix_ts>"
   - HMAC-SHA256(CLIENT_SECRET, payload) → signature
   - Token format: "<random>.<ts>.<hmac-hex>"
   - Self-verifying — NO server-side storage required

3. App builds authorization URL:
   https://login.microsoftonline.com/<tenant>/oauth2/v2.0/authorize
   ?client_id=<ENTRA_CLIENT_ID>
   &response_type=code
   &redirect_uri=<ENTRA_REDIRECT_URI>
   &scope=openid profile
   &state=<signed-token>

4. Browser redirects to Microsoft login page

5. User authenticates (password + MFA if required by conditional access)

6. Microsoft validates request, checks app role assignment

7. Microsoft redirects to:
   https://<app-fqdn>/?code=<auth_code>&state=<signed-token>
   — This is a NEW HTTP GET request
   — Streamlit creates a NEW WebSocket session
   — st.session_state from step 2 is GONE

8. app.py reads st.query_params:
   code           = st.query_params.get("code")
   returned_state = st.query_params.get("state")

9. App calls consume_state(returned_state) → src/entra_auth.py
   - Splits token: sig = last segment, payload = everything before
   - Recomputes HMAC-SHA256(CLIENT_SECRET, payload)
   - hmac.compare_digest(expected, sig) — constant-time comparison
   - Checks timestamp TTL (10 minutes)
   - Returns True (valid) or False (tampered / expired)
   - No database lookup needed — works across restarts and replicas

10. If state invalid → "OAuth2 state mismatch" error, prompt re-login

11. App POSTs to token endpoint:
    POST https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token
    Body: grant_type=authorization_code, code, client_id, client_secret, redirect_uri

12. Microsoft returns access_token + id_token (RS256 JWT)

13. App verifies id_token signature via PyJWKClient (JWKS endpoint):
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=CLIENT_ID
    )

14. App extracts user identity:
    st.session_state["entra_user"] = {
        "email":  claims["preferred_username"],
        "name":   claims["name"],
        "oid":    claims["oid"],       # stable object ID
        "role":   claims.get("roles", ["contributor"])[0],
    }
    st.session_state["authenticated"] = True

15. Page re-renders → _is_authenticated() returns True → main app shown
```

### 9.4 Why HMAC-signed stateless tokens

**The problem with server-side nonce storage on Azure Container Apps:**

The original implementation stored state nonces in a module-level Python dict. This worked locally but failed on Azure because `min-replicas=0` causes scale-to-zero — the container shuts down between the auth request (step 2) and Microsoft's callback (step 7). Every container restart cleared the nonce dict, causing "state mismatch" on every login attempt.

**The fix — stateless HMAC-signed tokens:**

```python
# src/entra_auth.py

def _sign(payload: str) -> str:
    return hmac.new(CLIENT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

def generate_state_nonce() -> str:
    raw     = secrets.token_urlsafe(32)
    ts      = str(int(time.time()))
    payload = f"{raw}.{ts}"
    return f"{payload}.{_sign(payload)}"    # self-contained signed token

def consume_state(state: str) -> bool:
    try:
        last_dot = state.rfind(".")
        sig      = state[last_dot + 1:]
        payload  = state[:last_dot]
        if not hmac.compare_digest(_sign(payload), sig):   # constant-time
            return False
        ts_str = payload.rsplit(".", 1)[-1]
        return time.time() - int(ts_str) < _STATE_TTL      # 10-minute TTL
    except Exception:
        return False
```

**Why this is correct CSRF protection:**
- The `state` parameter is opaque to Microsoft — it passes it through unchanged
- An attacker cannot forge a valid state without knowing `ENTRA_CLIENT_SECRET`
- `hmac.compare_digest` prevents timing side-channel attacks
- TTL prevents replay of old tokens
- Works identically across all replica instances and container restarts

### 9.5 Role mapping

| Entra app role | Capability |
|---|---|
| `admin` | All features, all models, Premium preset, live Jira push, all run history |
| `contributor` | Run synthesis, edit stories, push to Jira with approval gate |
| `viewer` | Read results and download exports only |
| _(no role assigned)_ | Defaults to `contributor` |

### 9.6 Fallback authentication modes (priority order)

1. `AUTH_DISABLED=1` — skip entirely (local development)
2. `ENTRA_TENANT_ID` set — Microsoft Entra ID OAuth2 (enterprise)
3. `config/auth.yaml` present — `streamlit-authenticator` YAML-based auth (fallback)
4. Demo mode (button on login page) — limited functionality

---

## Part 10 — Web UI Internals

### 10.1 Why Streamlit

- Python-native — no separate frontend stack, no JavaScript
- Hot reload during development — change a prompt, see it in the UI in seconds
- Built-in widgets: `file_uploader`, `selectbox`, `columns`, `tabs`, `@st.dialog`
- Custom CSS injection via `st.markdown(..., unsafe_allow_html=True)`
- `st.session_state` for within-session state

**Constraint:** Streamlit's WebSocket-per-session model means each user's browser has an independent Python execution context. State is not shared between users. This is correct — each synthesis run is user-specific.

### 10.2 Application entry point

`app.py` is 4,396 lines. Key sections:
- Lines 1–130: imports, `sys.path` setup, startup checks, Ollama auto-start
- Lines 131–200: Entra auth imports, `_current_user` initialization
- Lines 1960–2010: `MODEL_PRESETS` definition
- Lines ~2280: Usage meter guard (only query usage when user is authenticated, not `"local"`)
- Lines ~3882: Cost computation + `record_pipeline_cost()` telemetry call
- Lines ~4300+: Output tabs rendering

### 10.3 Pipeline progress display

A row of five numbered stage dots connected by a track line. Each dot has three visual states:

| State | Visual |
|-------|--------|
| `idle` | Grey ring |
| `active` | Silver fill with pulse animation |
| `done` | Black ring |

The active state uses `box-shadow` pulsing. The dot-track is updated via `st.session_state["stage_states"]`, which is set by the `progress_callback` passed into `orchestrator.run()`.

### 10.4 Threading model for a run

```
Main thread (Streamlit render loop)
    │
    ├─ Renders sidebar + progress area
    ├─ Polls st.session_state["stage_states"] every 300ms
    └─ Re-renders progress dot-track with each poll

Background thread (spawned by "Synthesize" button)
    │
    ├─ Calls orchestrator.run(inputs, progress_callback)
    ├─ progress_callback updates st.session_state["stage_states"]
    └─ On completion: sets st.session_state["result"] = synthesis_result

Cancel button (main thread)
    │
    └─ Sets _cancel_event (threading.Event)
       Background thread checks at each stage boundary
```

### 10.5 Output tabs

Six tabs are rendered after a run completes:

1. **Epics ({n} stories)** — card view with epic title, description, story count. Toggle to `st.data_editor` for inline editing. Edits flow to downloads.
2. **Gaps ({n})** — finding cards with title, description, related story IDs, evidence quote.
3. **Conflicts ({n})** — story–constraint conflict cards with severity pills.
4. **Duplicates ({n})** — side-by-side comparison of new story vs. existing Jira ticket via `@st.dialog`. Confidence level and similarity reason shown below.
5. **Guardrails ({n})** — security and quality findings from `InputSanitizer`, `OutputScanner`, and `guardrails.py`.
6. **Audit trail** — raw Markdown rendered directly. Prompts and responses in collapsible `<details>` blocks.

### 10.6 Run history

`src/ui/run_history.py` — Modal dialog showing past runs from `logs/runs/<user_id>/` with:
- Search/filter bar (by source label, model, timestamp)
- Date-bucket grouping (Today / Yesterday / This week / Older)
- Per-row Load and Delete buttons
- Aggregate strip: total runs, total stories drafted, cumulative estimated cost
- Org-wide cost summary (admin view) — spend per user, this month and all-time

**Per-user directory sanitization:**
```python
def _user_runs_dir(user_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (user_id or "anonymous"))
    return RUNS_DIR / safe
```
This replaces dots and special characters (including `@`) with underscores — consistent across `app.py`, `rate_limiter.py`, and `src/ui/run_history.py`.

### 10.7 State persistence across Streamlit reloads

| What | How |
|------|-----|
| Model preset selection | `.ui_state_<HOSTNAME>.json` file |
| Per-stage model overrides | Same file |
| Run history | `logs/runs/<user_id>/<timestamp>_<id>.json` |
| Authentication state | `st.session_state["entra_user"]` + `"authenticated"` |

---

## Part 11 — Docker & Azure Deployment

### 11.1 Dockerfile

**Base image:** `python:3.11-slim-bookworm` (not 3.13 — locked to match CI/CD Python version)

**Key design decisions:**

1. **PyTorch CPU wheels first:** A dedicated `RUN pip install torch --index-url https://download.pytorch.org/whl/cpu` layer runs before `requirements.txt`. This bakes the CPU-only PyTorch wheel (~700 MB) into a stable, cacheable layer and prevents the CUDA 3 GB wheels from being pulled when `sentence-transformers` is installed via the main requirements file.

2. **Non-root user:** Container runs as `appuser` (UID 1000). Streamlit requires no root privileges, and running as root in containers is a security anti-pattern.

3. **Pre-cached embedding model:** `RUN python src/warmup.py` downloads and bakes `all-MiniLM-L6-v2` (~22 MB) into the image at build time. Without this, the first synthesis run after container startup would spend 20–30 seconds downloading from HuggingFace.

4. **Health check:** `curl --fail http://localhost:8501/_stcore/health` every 30s with a 60s start period.

```dockerfile
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only PyTorch first — prevents CUDA 3 GB wheels
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py entrypoint.sh ./
COPY .streamlit/ ./.streamlit/
COPY src/ ./src/
COPY prompts/ ./prompts/
COPY samples/ ./samples/
COPY evaluation/ ./evaluation/
COPY config/ ./config/

# Bake embedding model — eliminates cold-start download
RUN python src/warmup.py

RUN mkdir -p outputs logs \
 && useradd --create-home --uid 1000 appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl --fail --silent http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
```

### 11.2 Azure infrastructure (Terraform)

**File:** `infra/terraform/main.tf` — `hashicorp/azurerm ~> 3.100`

**Remote state:** Azure Blob (`rg-tfstate` / `stbacklogstate` / container `tfstate` / key `backlog-synthesizer.tfstate`). Bootstrap storage created by the workflow before `terraform init`.

**Resources provisioned:**

| Resource type | Name pattern | Purpose |
|---|---|---|
| `azurerm_resource_group` | `rg-backlog-<env>` | Container for all resources |
| `azurerm_user_assigned_identity` | `id-backlog-<env>` | Managed identity for Container App |
| `azurerm_container_registry` | Configurable via `var.acr_name` | Private Docker image storage |
| `azurerm_key_vault` | Configurable via `var.keyvault_name` | All 10 application secrets |
| `azurerm_redis_cache` | `redis-backlog-<env>` | Basic C0 (250 MB), TLS-only, port 6380 |
| `azurerm_storage_account` | Configurable | Azure Files shares |
| `azurerm_storage_share` | `data` + `outputs` | Persistent log and output directories |
| `azurerm_log_analytics_workspace` | `law-backlog-<env>` | Container App diagnostics |
| `azurerm_container_app_environment` | Configurable | Shared networking + logging |
| `azurerm_container_app` | Configurable via `var.container_app_name` | The running application |

**Key Vault secrets stored (10 total):**
`ANTHROPIC-API-KEY`, `GOOGLE-API-KEY`, `JIRA-API-TOKEN`, `GITHUB-TOKEN`, `ENTRA-CLIENT-SECRET`, `OTEL-EXPORTER-OTLP-HEADERS`, `ACR-ADMIN-PASSWORD`, `AUTH-COOKIE-SECRET`, `SLACK-WEBHOOK-URL`, `REDIS-URL`

**Redis connection string format:** `rediss://:${access_key}@${hostname}:6380/0`  
The double-s (`rediss://`) is required — Azure Cache for Redis enforces TLS and rejects non-TLS connections.

**Container App lifecycle:**
```hcl
lifecycle {
  ignore_changes = [
    template[0].container[0].image,  # image managed by deploy.yml, not Terraform
  ]
}
```
The `image` key is excluded from Terraform management so `deploy.yml` can update the revision without Terraform fighting it. All secrets and env vars are owned by Terraform.

**Key Terraform variables:**

| Variable | Purpose |
|----------|---------|
| `environment` | `staging` / `production` |
| `location` | Azure region |
| `jira_base_url`, `jira_email`, `jira_project_key` | Jira configuration |
| `entra_tenant_id`, `entra_client_id`, `entra_tenant_domain`, `entra_redirect_uri` | SSO config |
| `otel_endpoint` | Grafana Cloud OTLP URL (maps from GitHub secret `OTEL_EXPORTER_OTLP_ENDPOINT`) |
| `otel_headers` | Grafana auth headers (maps from `OTEL_EXPORTER_OTLP_HEADERS`) |
| `anthropic_api_key`, `google_api_key`, `jira_api_token`, `entra_client_secret` | Sensitive — stored in Key Vault |

### 11.3 GitHub Actions CI/CD

**Workflows:**

| File | Trigger | Purpose |
|------|---------|---------|
| `.github/workflows/ci.yml` | Push to any branch, PRs | Lint (ruff) + unit tests |
| `.github/workflows/deploy.yml` | Push to `main` (staging) / manual (staging or production) | Test → build → deploy |
| `.github/workflows/terraform.yml` | Push to `infra/terraform/**` / manual | Terraform plan + apply |

**`deploy.yml` job chain:**

```
test ──► build-and-push ──► deploy ──► notify-failure (on failure only)
```

**Job 1 — test:**
- Python 3.11
- pip cache keyed on `requirements.txt`
- `ruff check src/ tests/ evaluation/ --select F,E9`
- `python -m pytest tests/ -q --tb=short`

**Job 2 — build-and-push:**
- `docker/build-push-action@v6` with GitHub Actions layer cache (`type=gha`)
- Tags image with `${{ github.sha }}` (immutable) and `latest` (floating)
- Pushes to ACR

**Job 3 — deploy:**
- `environment: staging` or `environment: production` (production requires manual approval via GitHub Environments)
- `az containerapp update --image "$IMAGE" --revision-suffix "${SHA:0:7}"`
- **Does NOT set env vars** — all env vars and secrets are owned by Terraform; setting them in deploy would cause a conflict
- Health check: polls `/_stcore/health` every 15s, 5 attempts
- Outputs the deployed URL to `$GITHUB_STEP_SUMMARY`

### 11.4 Required GitHub secrets

| Secret | Used by |
|--------|---------|
| `AZURE_CREDENTIALS` | `az login` in all workflows |
| `ACR_REGISTRY` | Docker push endpoint |
| `CONTAINERAPP_NAME` | `az containerapp update` target |
| `AZURE_RESOURCE_GROUP` | Resource group for all `az` commands |
| `ANTHROPIC_API_KEY` | App runtime (read from Key Vault) |
| `TF_VAR_ANTHROPIC_API_KEY` | Terraform apply — writes to Key Vault |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Terraform variable `otel_endpoint` |
| `OTEL_EXPORTER_OTLP_HEADERS` | Terraform variable `otel_headers` |

---

## Part 12 — Observability

### 12.1 Prometheus metrics

**File:** `src/metrics.py`  
**Endpoint:** `:9090/metrics` (port configurable via `METRICS_PORT`)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `backlog_syntheses_total` | Counter | `status` (success/failure/cancelled) | Total pipeline runs |
| `backlog_synthesis_duration_seconds` | Histogram | — | Wall-clock time per run |
| `backlog_synthesis_cost_usd` | Histogram | — | LLM USD cost per run |
| `backlog_tokens_total` | Counter | `model`, `direction` (input/output) | Cumulative token consumption |
| `backlog_llm_errors_total` | Counter | `provider` (anthropic/google/ollama) | LLM API errors |
| `backlog_active_synthesis` | Gauge | — | 1 while a run is in progress |
| `backlog_circuit_breaker_state` | Gauge | `provider` | 0=CLOSED, 1=OPEN, 2=HALF_OPEN |

**API:**
```python
start_metrics_server()               # call once at Streamlit startup — idempotent
record_synthesis_start()             # set active gauge to 1
record_synthesis_end(
    status="success",
    elapsed_seconds=142.3,
    cost_usd=0.87,
    token_usage={"total": {"input": 12000, "output": 8000}},
    model="claude-sonnet-4-5"
)
record_llm_error(provider="anthropic")
record_circuit_breaker_state(provider="google", state_value=1)  # OPEN
```

**Histogram buckets:**
- Duration: `[15, 30, 60, 90, 120, 180, 300, 600]` seconds
- Cost: `[0.01, 0.05, 0.10, 0.25, 0.50, 1.00, 2.00, 5.00]` USD

### 12.2 OpenTelemetry tracing

**File:** `src/telemetry.py`

**Configuration:**
```
OTEL_ENABLED=1                          # required to activate
OTEL_SERVICE_NAME=backlog-synthesizer   # default
OTEL_EXPORTER_OTLP_ENDPOINT            # Grafana Cloud OTLP gateway
OTEL_EXPORTER_OTLP_HEADERS             # "Authorization=Basic <base64(instanceId:apiKey)>"
```

**Span hierarchy:**
```
pipeline.run  [run.id, run.model_summary, run.preset]
├── stage.parser                [stage.name, stage.model, stage.input_chars]
├── stage.constraint_extractor  [stage.name, stage.model, stage.input_chars]
├── stage.story_writer          [stage.name, stage.model]
│   └── llm.call                [provider, model, input_tokens, output_tokens]
├── stage.epic_decomposer       [stage.name, stage.model]
├── stage.gap_detector          [stage.name, stage.model]
│   └── pipeline.node.detect_gaps  [pipeline.node, pipeline.run_id]
└── guardrail.priority_ratio    [severity]
    guardrail.story_grounding   [severity]
```

**OTel metrics exported (separate from Prometheus):**
- `backlog_syntheses_total` — counter with `status` and `preset` labels
- `backlog_synthesis_duration_seconds` — histogram
- `backlog_synthesis_cost_usd` — histogram (recorded via `record_pipeline_cost()` in `app.py`)
- `backlog_tokens_total` — counter per stage and type
- `backlog_active_synthesis` — up/down counter (inc in orchestrator start, dec at end)
- `guardrail_findings_total` — counter per severity

**When `OTEL_ENABLED` is not set:** All telemetry functions are no-ops via `_NoopSpan`. The app runs identically with zero telemetry overhead.

### 12.3 Structured log fields for Azure Monitor

Key fields emitted to stdout (captured by Container App diagnostics):

**Pipeline-level:**
`run_id`, `user`, `status`, `elapsed_seconds`, `cost_usd`, `source_label`, `transcript_chars`, `constraint_chars`, `existing_ticket_count`, `preset`, `redact_pii`, `live_jira`

**Stage-level:**
`stage`, `stage.model`, `stage.input_chars`, `stage_duration_seconds`, `status`

**LLM call-level:**
`agent`, `tool`, `input_tokens`, `output_tokens`, `tokens_used`, `model`, `direction`, `provider`, `prompt_chars_actual`, `response_chars_actual`

**Error/alert-level:**
`error`, `partial`, `circuit_breaker_state`, `findings[].code`, `findings[].severity`, `findings[].message`

**Example KQL query for Azure Monitor:**
```kql
ContainerAppConsoleLogs_CL
| where ContainerName_s == "backlog-synthesizer"
| where Log_s has "pipeline_completed" or Log_s has "stage_duration"
| project TimeGenerated, status_s, elapsed_seconds_d, cost_usd_d, user_s, stage_s, tokens_used_d
| order by TimeGenerated desc
```

---

## Part 13 — Cost & Budget Model

### 13.1 Token counts per agent (typical run)

A typical NorthStar sprint planning run (1 meeting transcript, 1 Confluence constraints page, 50 existing Jira tickets):

| Stage | Preset model | Input tokens | Output tokens | Cost (claude-sonnet-4-5 at $3/$15 per MTok) |
|-------|-------------|-------------|---------------|------|
| parser | claude-sonnet-4-5 | ~2,500 | ~1,200 | ~$0.008 |
| constraint | claude-sonnet-4-5 | ~3,000 | ~800 | ~$0.009 |
| story_writer | claude-sonnet-4-5 | ~3,500 | ~6,000 | ~$0.100 |
| epic_decomposer | claude-sonnet-4-5 | ~5,000 | ~4,000 | ~$0.075 |
| gap_detector | claude-sonnet-4-5 | ~4,500 | ~3,000 | ~$0.058 |
| **Total (Premium)** | | **~18,500** | **~15,000** | **~$0.25** |
| **Total (Balanced)** | Gemini + Claude | — | — | **~$0.01** |
| **Total (Free)** | gemini-2.5-flash | — | — | **~$0.00** |

Story Writer uses `max_tokens=16,000` and Gap Detector uses `max_tokens=16,000` — both were increased from 4,000 to prevent JSON truncation on complex backlogs.

### 13.2 Pre-run cost estimation

Before each synthesis, the UI estimates cost from character counts (4 chars/token heuristic) and the selected preset's per-stage model costs. If the estimated cost would push the user over their `RATE_LIMIT_COST_PER_DAY` limit, the synthesis is blocked.

### 13.3 Budget enforcement — rate_limiter + budget_store

**File:** `src/rate_limiter.py` + `src/budget_store.py`

**Rate limits:**
- `RATE_LIMIT_RUNS_PER_HOUR` (default 10) — rolling 1-hour window per user
- `RATE_LIMIT_COST_PER_DAY` (default $5.00) — daily USD cap per user

**Budget store backends:**

**Redis (when `REDIS_URL` is set):**
- Key: `budget:<user_id>:<YYYYMMDD>` (hash with `spend_usd` field)
- Rate keys: `rate:<user_id>:h:<YYYYMMDDHH>` and `rate:<user_id>:d:<YYYYMMDD>`
- TTL: 25 hours
- Atomic Lua script (`_LUA_RESERVE`) for reserve/settle — prevents race conditions across Container App replicas

**File fallback:** Reads `logs/runs/<user_id>/*.json` and sums `cost_usd` fields for today's date prefix.

**Usage summary returned to sidebar:**
```python
{
    "runs_last_hour": int,
    "max_runs_per_hour": int,
    "cost_today_usd": float,
    "max_cost_per_day_usd": float,
}
```

**Usage meter guard in `app.py`:**
```python
if _can_run() and _current_user not in ("local", "unknown", "anonymous", ""):
    _usage = get_usage_summary(_current_user)
```
This guard prevents the usage meter from querying with the pre-authentication default `"local"` user ID, which would look for a non-existent directory.

---

## Part 14 — Error Handling & Resilience

### 14.1 LLM API errors

| Failure | Caught as | Behavior |
|---------|-----------|----------|
| API key missing | `AgentError` at init | Alert banner with setup instructions |
| Rate limited (HTTP 429) | Transient — retried with backoff | Up to `AGENT_MAX_RETRIES` attempts |
| Network failure | `APIConnectionError` → `AgentError` | Retry, then circuit breaker trips |
| No JSON in response | `AgentError("No JSON found")` | First 300 chars of raw response shown |
| Truncated JSON | Auto-repair attempted | Unclosed string fixed by trimming |
| Circuit breaker OPEN | `AgentError` | Immediate failover to next provider |
| All providers exhausted | `AgentError` | Stage fails, graceful degradation kicks in |

### 14.2 Jira / Confluence errors

| Failure | HTTP | Behavior |
|---------|------|----------|
| Invalid credentials | 401 | Alert: "check JIRA_EMAIL and JIRA_API_TOKEN" |
| Project not found | 404 | Alert: "project 'NS' not found" |
| JQL syntax error | 400 | Alert with Atlassian error details |
| Rate limited | 429 | Alert: "retry in 60 seconds" |
| Confluence page not found | 404 | "page not found — skipping constraints" (non-fatal) |
| Jira ticket creation 400 | 400 | Defensive retry: drop parent → drop labels → drop type |

### 14.3 Authentication errors

| Failure | Where | User sees |
|---------|-------|-----------|
| `AADSTS50105` (not assigned) | Microsoft redirect | Entra error page. Fix: assign user in Enterprise Applications |
| State mismatch (CSRF) | `app.py` callback | "Login failed: OAuth2 state mismatch" |
| State expired (>10 min) | `consume_state()` | "Login expired — please sign in again" |
| Client secret expired | `exchange_code_for_token` | "Authentication configuration error" |
| Missing `ENTRA_CLIENT_ID` | App init | Warning banner — demo mode offered |

### 14.4 Azure deployment errors

| Error | Root cause | Fix |
|-------|-----------|-----|
| `SecretRefNotFound: SecretRef 'redis-url'` | `lifecycle { ignore_changes = [secret] }` prevented the new secret from being added while the env var referencing it was applied | Remove `secret` from `ignore_changes` |
| `non_ssl_port_enabled` attribute error | Deprecated attribute name `enable_non_ssl_port` | Use `non_ssl_port_enabled = false` |
| `TF_VAR_OTEL_ENDPOINT` not found | GitHub secret named `OTEL_EXPORTER_OTLP_ENDPOINT` but terraform.yml referenced `TF_VAR_OTEL_ENDPOINT` | Fix secret reference in terraform.yml |
| `MissingSubscriptionRegistration` | Resource provider not registered | `az provider register --namespace Microsoft.Cache --wait` |
| Pipeline metric name mismatch | App emitted `pipeline_*` names but Grafana queries `backlog_*` | Renamed all metrics in `telemetry.py` |
| `runs/hr` counter not updating on Azure | Three `_user_runs_dir` functions had inconsistent sanitizers — `run_history.py` used `"-_."` (dots kept) while `app.py` and `rate_limiter.py` used `"-_"` (dots replaced) | Made all three identical |

---

## Part 15 — How AI Was Used to Build This

### 15.1 As a pipeline architect

Before writing a line of code, the pipeline architecture was designed with Claude:

- *"What is the right number of agents for a backlog synthesis pipeline? How should they hand off?"*
- *"What are the failure modes of combining extraction and deduplication in one prompt?"*

The insight that emerged: **one agent, one reasoning task.** Mixing "write stories" and "check for duplicates" in one prompt produces a model that does both badly. Specialist agents produce dramatically better results than a generalist do-everything prompt.

### 15.2 As an auth debugger

The CSRF + Streamlit session reset bug was subtle. The dialogue:

- *"The OAuth2 state nonce is stored before the redirect. After the redirect, the state is gone. Why?"*

Claude identified the root cause: Streamlit creates a new WebSocket connection per page navigation, and Microsoft's redirect is a fresh HTTP GET. The stateless HMAC-signed token approach emerged from this conversation. It solved the Azure scale-to-zero problem at the same time — no server-side state means no container restart issues.

### 15.3 As a devops engineer

The Azure deployment pipeline went through multiple iterations. Key AI-assisted discoveries:

1. `az containerapp update` does not accept `--registry-server` flags — only `az containerapp create` does. Use `az containerapp registry set` as a separate step.
2. Adding a new secret to a Container App while `lifecycle { ignore_changes = [secret] }` is active causes `SecretRefNotFound` — the env var referencing the secret is applied but the secret itself is blocked.
3. Terraform's `non_ssl_port_enabled` deprecation — `enable_non_ssl_port` silently fails without warning on newer provider versions.
4. GitHub Actions Terraform workflow secret naming — `TF_VAR_OTEL_ENDPOINT` doesn't match the secret name `OTEL_EXPORTER_OTLP_ENDPOINT`, so Terraform receives an empty string and the Container App has no OTLP endpoint.
5. `rediss://` (double-s) is required for Azure Cache for Redis — the standard `redis://` scheme is rejected because non-SSL port is disabled.

### 15.4 As a debugging assistant

The `runs/hr` counter not updating on Azure was traced to a three-way inconsistency:

- `src/ui/run_history.py` sanitized user IDs with `"-_."` (dots kept) — `email@domain` → `email@domain` directory
- `app.py` and `rate_limiter.py` used `"-_"` (dots replaced) — `email@domain` → `email_domain` directory

Budget store wrote runs to the dotted directory, rate limiter looked in the underscore directory — always found zero runs. AI-assisted code search across three files in seconds; manual search would have taken much longer.

### 15.5 Development insights

1. **Test before pushing.** Multiple Terraform failures were caused by fixes not verified locally. Run `terraform validate` and `az containerapp update --dry-run` before every push.

2. **Lifecycle block conflicts in Terraform.** `ignore_changes` blocks feel like "safe" settings but create subtle write-conflict bugs when new resources reference things being ignored. Only ignore exactly what CI/CD owns (`image`), nothing else.

3. **Azure secret name conventions.** Terraform variable names use underscores (`otel_endpoint`). GitHub secret names must match what the workflow references exactly. Document the mapping explicitly.

4. **Metric name contracts between app and dashboard.** The Grafana dashboard is a consumer of metric names. Changing metric names in code without updating dashboard queries silently breaks all panels — traces still flow (Tempo is name-agnostic) but every metric panel shows empty.

5. **Graceful degradation > fail-fast for agent pipelines.** When Gap Detector fails, don't blow up the run. The user already paid for four API calls. Return what you have and flag the failure clearly.

---

## Part 16 — Interview Q&A

### Q1: "Walk me through what this project does."

The NorthStar Backlog Forge takes raw engineering inputs — meeting transcripts, architecture wikis, existing Jira tickets — and runs them through a five-stage orchestrated pipeline to produce a structured sprint backlog. The five agents each have one job: parse topics, extract constraints, write stories, group into epics, and detect duplicates and gaps. The LLM runs at exactly those five points; everything else is deterministic Python.

It's deployed as a containerized Streamlit app on Azure Container Apps with Microsoft Entra SSO for enterprise authentication, Azure Cache for Redis for cross-replica budget enforcement, and OpenTelemetry spans exported to Grafana Cloud. GitHub Actions handles CI/CD — push to main, Docker image builds, Container App deploys the new revision.

---

### Q2: "Why five agents instead of one large prompt?"

Early prototypes used a single prompt. Three problems emerged: the model skipped duplicate detection on large backlogs; priority scores became inconsistent mid-output; and story evidence was lost — you couldn't trace which meeting quote generated which story.

Splitting into specialists solved all three. Each agent has exactly one reasoning task. The Story Writer doesn't have to "remember" to also check for duplicates and enforce constraints. The extra API calls are worth it.

---

### Q3: "You don't use LangGraph — why?"

The orchestrator is a custom `Orchestrator` class (`src/orchestrator.py`) rather than a LangGraph StateGraph. The pipeline has a fixed execution order with one parallel fan-out (Agents 1 and 2), which the orchestrator handles directly by running both stages before proceeding to Agent 3. This was simpler to reason about and debug than a graph framework for this specific topology.

`src/pipeline.py` (960 lines) implements the LangGraph-style `StateGraph` version and the `Orchestrator` wraps it. The inter-agent communication happens through a shared `MemoryStore` instance — a `TypedDict`-backed key/value store where each agent writes only its own keys.

---

### Q4: "How does the SSO authentication flow work?"

When the user clicks Sign in with Microsoft, the app generates a stateless HMAC-signed state token — a random nonce combined with a timestamp, signed with `ENTRA_CLIENT_SECRET`. This token goes in the OAuth2 `state` parameter.

After Microsoft authentication, the browser redirects back to our app with `?code=...&state=...`. This is a new HTTP GET — Streamlit creates a brand new WebSocket session, so `st.session_state` is empty. The state token is verified by re-computing the HMAC signature and checking the TTL (10 minutes). No database lookup needed — works across container restarts and replicas.

The auth code is then exchanged for an ID token, which is verified against Microsoft's JWKS endpoint using `PyJWT`. Claims give us the user's email, name, and app role.

---

### Q5: "How does duplicate detection work?"

Two-step hybrid approach. First, `sentence-transformers` (`all-MiniLM-L6-v2`) embeds all existing Jira tickets and each new story. Cosine similarity at threshold 0.6 retrieves the top-5 candidates per story. This runs in milliseconds with no API cost.

Second, those candidates are passed to the Gap Detector LLM with the question: "Given these top-5 similar tickets, is this new story actually a duplicate?" The LLM makes the nuanced judgment — "similar topic but different scope" vs. "genuine duplicate." This embed → narrow → judge pattern is the same RAG approach used in production retrieval systems.

---

### Q6: "What happens if one agent fails?"

Graceful degradation. Each pipeline stage catches its own exceptions and writes to `stage_errors`. Earlier stage outputs are never lost.

If the Story Writer fails, the user gets zero stories but the transcript summary and topics are preserved in the output. If the Gap Detector fails, the user gets stories and epics without conflict/duplicate analysis, with a warning banner. The principle: never lose work the user already paid for. Earlier API calls cost real money and time.

---

### Q7: "Why is Azure Cache for Redis in this project?"

Rate limiting and budget enforcement need to be consistent across Container App replicas. If each replica tracked runs independently in its own file system, a user could hit the limit on replica A and still make calls on replica B.

Redis provides atomic cross-replica tracking. The budget store uses a Lua script (`_LUA_RESERVE`) to atomically check and increment spend in a single round-trip, preventing TOCTOU race conditions. The Redis connection string uses `rediss://` (double-s) because Azure Cache for Redis enforces TLS — the non-SSL port is disabled.

---

### Q8: "Why did the Grafana dashboard not show any data initially?"

Two-part issue. First, the GitHub Terraform workflow referenced secrets named `TF_VAR_OTEL_ENDPOINT` and `TF_VAR_OTEL_HEADERS`, but the actual GitHub secrets were named `OTEL_EXPORTER_OTLP_ENDPOINT` and `OTEL_EXPORTER_OTLP_HEADERS`. Terraform received empty strings, so the Container App had no OTLP endpoint — traces were printed to stdout (ConsoleSpanExporter) but never shipped to Grafana.

Second, even after fixing the endpoint, the Grafana dashboard panels still showed empty because all metric names in `telemetry.py` used `pipeline_*` prefixes (`pipeline_runs_total`, `pipeline_duration_seconds`) while the dashboard queries used `backlog_*` prefixes (`backlog_syntheses_total`, `backlog_synthesis_duration_seconds`). Traces worked fine in Tempo (name-agnostic), but every Mimir metric panel returned no data. Fixed by renaming all metrics in `telemetry.py` to match the dashboard contract.

---

### Q9: "How does the audit trail fingerprinting work?"

After each event is appended to the log, its SHA-256 hash is chained with the previous hash: `new_hash = sha256(prev_hash + event_bytes)`. The final hash is stored in the synthesis output JSON as `audit_chain_fingerprint`.

If anyone tampers with a stored audit event — changing a prompt to hide what the model was asked, or changing a response — recomputing the chain hash from the modified events produces a different fingerprint. The stored fingerprint won't match. This doesn't prevent tampering, but makes it detectable. The pattern is borrowed from blockchain and certificate transparency logs. Events are persisted to SQLite (`logs/audit_chain.db`) and `verify_chain()` re-derives hashes from the stored rows.

---

### Q10: "Walk me through the model preset system."

Four presets are defined in `app.py` as `MODEL_PRESETS` — a `dict[str, dict[str, str]]` mapping preset name to per-stage model IDs. The Streamlit sidebar shows preset chips. The user's selection is stored in `st.session_state["model_preset"]` and persisted to `.ui_state_<HOSTNAME>.json` across reloads.

When Synthesize is clicked, the selected preset dict is passed as `models=` into `orchestrator.run()`. Each stage calls `_build_tool_for_model(model_id)` which returns a `ClaudeTool`, `GeminiTool`, or `OllamaTool` instance. The agent never knows which concrete class it holds — it only calls `tool.call_for_json(prompt, max_tokens=N)`. This is the Strategy pattern.

---

### Q11: "What is the `VisionAttachment` type and how is it used?"

```python
@dataclass
class VisionAttachment:
    media_type: str    # e.g. "image/png", "image/jpeg", "image/webp"
    data_b64: str      # base64-encoded bytes
    label: str = ""    # optional description
```

Users can upload whiteboard photos or architecture diagrams alongside the text transcript. `app.py` reads uploaded images, encodes them as base64, and wraps them in `VisionAttachment` instances. These are passed through `orchestrator.run(vision_attachments=[...])` to `ParserAgent.run(vision_attachments=[...])`.

The `ClaudeTool` sends images as content blocks before the text block in the API call. When vision attachments are present, the `auto_switch` logic in the orchestrator ensures the parser uses a Claude model even if a Gemini model was selected — Gemini doesn't support vision in the current wrapper.

---

### Q12: "What was the hardest debugging problem you solved?"

The `runs/hr` counter not updating on Azure. The symptom: the sidebar always showed 0 runs in the last hour regardless of how many synthesis runs had been completed.

Three separate `_user_runs_dir` functions existed: one in `src/ui/run_history.py`, one in `app.py`, and one in `src/rate_limiter.py`. They all sanitized the user ID to a safe directory name, but they used slightly different character sets:

- `run_history.py` used `c in "-_."` — dots were kept, so `user@domain.com` → `user@domain.com` directory
- `app.py` and `rate_limiter.py` used `c in "-_"` — dots were replaced, so `user@domain.com` → `user_domain_com` directory

Budget store (which imports from `run_history.py`) created the dotted directory. Rate limiter looked in the underscore directory — always found zero files, always returned 0 runs. Fixed by making all three functions identical, using `"-_"` (dots replaced).

---

### Q13: "How do you handle the Streamlit sidebar rendering before auth resolves?"

Streamlit executes `app.py` top-to-bottom on every interaction, including the initial page load. The sidebar renders before the Entra auth callback has been processed. At that point, `_current_user` is still `"local"` (the module-level default).

Without a guard, the usage meter would call `get_usage_summary("local")`, look for a `logs/runs/local/` directory, find nothing, and display 0/10 runs — misleadingly suggesting the user has used none of their quota.

The fix is a simple guard:
```python
if _can_run() and _current_user not in ("local", "unknown", "anonymous", ""):
    _usage = get_usage_summary(_current_user)
```
Once Entra auth completes and `_current_user` is set to the actual email, the meter starts showing real usage.

---

### Q14: "What failure mode worries you most?"

Hallucination in the Story Writer. The system prompt says "only produce stories grounded in the provided source material" and the prompt includes "return `{'stories': []}` rather than inventing requirements if no actionable stories can be derived." But on ambiguous or sparse input, the model can still produce confident-sounding stories with requirements that weren't in the source material.

Mitigations in place: the `evidence` block forces the model to cite a source quote for each story; the `source_topic_id` field traces each story back to a topic that traces back to the transcript; the auto-repair logic catches invalid topic references; and the human approval step before Jira push is non-skippable in the UI. The tool generates drafts for engineers to validate — it doesn't write production specs autonomously.

---

### Q15: "How did you approach cost optimization?"

Three complementary techniques:

1. **Prompt caching:** The system prompt (~1,300 words) is sent with `cache_control: ephemeral` on every Claude call. Cached input tokens cost ~10% of uncached. With five Claude calls per Premium run and a shared system prompt, ~80–90% of system prompt tokens are served from cache on calls 2–5.

2. **Hybrid duplicate detection:** Local `sentence-transformers` handles candidate retrieval (milliseconds, zero API cost). The LLM only judges the top-5 candidates per story — not the full backlog. Sending 200 Jira tickets to the LLM for every story would cost significantly more and take 3× longer.

3. **Model presets:** The Balanced preset uses Gemini 2.5 Flash for mechanical extraction stages (parse, constraint, epic decompose) and Claude Sonnet only for reasoning-heavy stages (story writing, gap detection). This cuts cost by ~60% vs. Premium while maintaining output quality on the stages that matter most.

---

## Appendix A — Environment Variables Reference

| Variable | Required | Default | Description |
|----------|---------|---------|-------------|
| `ANTHROPIC_API_KEY` | For Claude | — | Claude API key |
| `ANTHROPIC_MODEL` | No | `claude-sonnet-4-5` | Default Claude model ID |
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` | For Gemini | — | Google Gemini API key |
| `GEMINI_MODEL` | No | `gemini-2.5-flash` | Default Gemini model ID |
| `AGENT_MAX_RETRIES` | No | `3` | LLM retry limit per stage |
| `JIRA_MODE` | No | `mock` | `mock` or `live` |
| `JIRA_BASE_URL` | Live Jira only | — | e.g. `https://tenant.atlassian.net` |
| `JIRA_EMAIL` | Live Jira only | — | Atlassian account email |
| `JIRA_API_TOKEN` | Live Jira only | — | Atlassian personal API token |
| `JIRA_PROJECT_KEY` | No | — | Default project key (e.g. `NS`) |
| `CONFLUENCE_MODE` | No | `mock` | `mock` or `live` |
| `CONFLUENCE_BASE_URL` | Live Confluence only | Same as Jira | Confluence URL |
| `CONFLUENCE_EMAIL` | Live Confluence only | Same as Jira | Confluence email |
| `CONFLUENCE_API_TOKEN` | Live Confluence only | Same as Jira | Confluence API token |
| `ATLASSIAN_MCP_ENABLED` | No | — | Set `1` to use MCP Atlassian server |
| `GITHUB_MCP_ENABLED` | No | — | Set `1` to use MCP GitHub server |
| `MEMORY_PERSISTENT` | No | — | Set `1` for NPZ file-backed vector cache |
| `USE_CHROMADB` | No | — | Set `1` for ChromaDB vector store |
| `REDIS_URL` | No | — | e.g. `rediss://:key@hostname:6380/0` |
| `AUDIT_DB_PATH` | No | `logs/audit_chain.db` | SQLite audit log path |
| `OUTPUTS_DIR` | No | `outputs/` | Synthesis output directory |
| `LOGS_DIR` | No | `logs/` | Log directory (Azure Files mount) |
| `OTEL_ENABLED` | No | — | Set `1` to enable OpenTelemetry |
| `OTEL_SERVICE_NAME` | No | `backlog-synthesizer` | OTel service name |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | For Grafana | — | Grafana Cloud OTLP gateway URL |
| `OTEL_EXPORTER_OTLP_HEADERS` | For Grafana | — | e.g. `Authorization=Basic <b64>` |
| `METRICS_PORT` | No | `9090` | Prometheus /metrics port |
| `ENTRA_TENANT_ID` | For SSO | — | Entra tenant GUID |
| `ENTRA_TENANT_DOMAIN` | For SSO | — | e.g. `tenant.onmicrosoft.com` |
| `ENTRA_CLIENT_ID` | For SSO | — | App registration client ID |
| `ENTRA_CLIENT_SECRET` | For SSO | — | App registration client secret |
| `ENTRA_REDIRECT_URI` | For SSO | `http://localhost:8501/` | OAuth2 callback URL |
| `AUTH_DISABLED` | No | — | Set `1` to skip auth (local dev only) |
| `RATE_LIMIT_RUNS_PER_HOUR` | No | `10` | Per-user hourly run cap |
| `RATE_LIMIT_COST_PER_DAY` | No | `5.00` | Per-user daily USD cap |
| `DAILY_BUDGET_USD` | No | `10.0` | Per-user daily budget cap |
| `STREAMLIT_SERVER_HEADLESS` | Docker | `true` | No browser in container |

---

## Appendix B — Azure Deployment Checklist

Before first deployment:

- [ ] App registration created in Azure Portal → Entra ID → App Registrations
- [ ] Redirect URI added: `https://<staging-fqdn>/` (get FQDN from first deploy)
- [ ] Service principal created and **Contributor role assigned at subscription level** (required for `az group create`)
- [ ] AcrPush role assigned to SPN on the ACR resource
- [ ] GitHub secrets added: `AZURE_CREDENTIALS`, `ACR_REGISTRY`, `CONTAINERAPP_NAME`, `AZURE_RESOURCE_GROUP`
- [ ] GitHub secrets added: `TF_VAR_ANTHROPIC_API_KEY`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`
- [ ] Run Terraform workflow — all resources provision
- [ ] Run deploy workflow — staging Container App updates
- [ ] Test SSO login through staging URL
- [ ] Assign your user/group in Entra → Enterprise Applications → NorthStar Backlog Forge → Users and Groups
- [ ] Verify `/_stcore/health` returns 200
- [ ] Verify Grafana dashboard shows traces (Tempo) and metrics (Mimir) after first synthesis run

---

## Appendix C — Key File Index

| File | Lines | Purpose |
|------|-------|---------|
| `app.py` | 4,396 | Streamlit UI — all rendering, auth, run handling, model presets |
| `src/orchestrator.py` | 1,244 | Pipeline entry point — stage sequencing, graceful degradation, OTel hooks |
| `src/pipeline.py` | 960 | LangGraph-style StateGraph implementation |
| `src/agents/parser_agent.py` | ~200 | Agent 1 — topic extraction from transcript + images |
| `src/agents/constraint_agent.py` | ~180 | Agent 2 — constraint extraction from wiki |
| `src/agents/story_writer_agent.py` | ~350 | Agent 3 — story drafting + auto-repair |
| `src/agents/epic_decomposer_agent.py` | ~250 | Agent 4 — epic grouping + task decomposition |
| `src/agents/gap_detector_agent.py` | ~320 | Agent 5 — duplicate/conflict/gap detection |
| `src/memory/store.py` | ~200 | MemoryStore — KV + vector backends |
| `src/memory/audit_log.py` | ~180 | AuditLog — SQLite persistence + chain fingerprinting |
| `src/tools/claude_tool.py` | ~250 | ClaudeTool — caching, vision, JSON extraction, retry |
| `src/tools/gemini_tool.py` | ~150 | GeminiTool — new google-genai SDK, error classification |
| `src/tools/ollama_tool.py` | ~120 | OllamaTool — local Ollama, health check, fallback |
| `src/tools/jira_tool.py` | ~300 | JiraTool — REST + MCP + mock, publish hierarchy |
| `src/tools/confluence_tool.py` | ~200 | ConfluenceTool — v2 API, format conversion, page creation |
| `src/tools/embedding_tool.py` | ~180 | sentence-transformers wrapper + cosine similarity dedup |
| `src/security.py` | ~300 | InputSanitizer (8 injection patterns) + OutputScanner (PII/toxicity) |
| `src/guardrails.py` | ~200 | Post-synthesis quality rules (6 checks) |
| `src/entra_auth.py` | ~250 | Microsoft Entra ID OAuth2 + HMAC-signed CSRF tokens |
| `src/budget_store.py` | ~280 | Redis + file-backed budget enforcement (atomic Lua reserve) |
| `src/rate_limiter.py` | ~160 | Per-user hourly/daily rate limiting |
| `src/circuit_breaker.py` | ~120 | Per-provider circuit breakers (CLOSED/OPEN/HALF_OPEN) |
| `src/metrics.py` | ~205 | Prometheus metrics server + `backlog_*` metric family |
| `src/telemetry.py` | ~340 | OpenTelemetry spans + OTel metrics + helper functions |
| `src/pricing.py` | ~80 | `estimate_cost_usd()` — per-model input/output token pricing |
| `src/ui/run_history.py` | ~393 | Run history dialog — date buckets, load/delete, org cost view |
| `src/ui/styling.py` | ~600 | CSS design tokens + component styles |
| `prompts/system_prompt.md` | ~50 | Shared system prompt — NorthStar Retail persona + operating principles |
| `prompts/parser_prompt.md` | ~60 | Agent 1 prompt — topic extraction schema + rules |
| `prompts/constraint_extractor_prompt.md` | ~50 | Agent 2 prompt — constraint extraction schema |
| `prompts/story_writer_prompt.md` | ~80 | Agent 3 prompt — story schema + AC rules |
| `prompts/epic_decomposer_prompt.md` | ~60 | Agent 4 prompt — epic/task schema |
| `prompts/gap_detector_prompt.md` | ~70 | Agent 5 prompt — conflict/gap schema |
| `Dockerfile` | ~60 | Docker build — CPU PyTorch, non-root user, warmup script |
| `.github/workflows/deploy.yml` | ~250 | 4-job deploy pipeline: test → build → deploy → notify |
| `.github/workflows/terraform.yml` | ~300 | Terraform plan + apply + Redis/Redis KV import |
| `.github/workflows/ci.yml` | ~60 | Lint + unit tests on every push |
| `infra/terraform/main.tf` | ~500 | Azure resources — ACR, Key Vault, Redis, Storage, Container App |
| `infra/terraform/variables.tf` | ~178 | All Terraform input variable definitions |
| `infra/terraform/outputs.tf` | ~52 | app_url, acr_login_server, redis_hostname, key_vault_uri |
| `tests/test_orchestrator.py` | ~200 | Full pipeline integration tests (mocked tools) |
| `tests/test_agents.py` | ~300 | Individual agent unit tests |
| `tests/test_guardrails.py` | ~150 | Guardrail rule tests |
| `tests/test_security_circuit_breaker.py` | ~120 | Circuit breaker state machine tests |
| `tests/test_hallucination.py` | ~100 | LLM output validation |
| `tests/test_vision.py` | ~80 | Vision attachment handling |
| `tests/test_redactor.py` | ~100 | PII redaction tests |
| `tests/test_jira_live.py` | ~80 | Jira live integration tests (needs credentials) |
| `tests/test_confluence_live.py` | ~80 | Confluence live integration tests (needs credentials) |
| `evaluation/run_evaluation.py` | ~250 | LLM-as-judge quality scoring |
| `scripts/capture_screenshots.py` | ~200 | Playwright screenshot automation |

---

*The multi-agent pipeline is the core of this system — but the most debugging time went not into prompt engineering but into Azure deployment plumbing, OAuth2 session management, metric naming contracts, and subtle environment-dependent bugs like the `_user_runs_dir` sanitizer inconsistency. AI agents do their reasoning jobs reliably once the infrastructure around them is correct. The lesson: get the scaffolding right first.*
