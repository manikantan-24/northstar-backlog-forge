# Architecture

A multi-agent Python system. A single orchestrator coordinates five specialized agents. Each agent has one reasoning job, calls Claude through a wrapped tool, and writes its findings to a shared memory store. Every agent decision goes into a structured audit log so a human reviewer can trace exactly how the final synthesis was produced.

## High-level data flow

```mermaid
flowchart TB
    User((User / CLI)):::user
    User --> Loader

    Loader["Input Loader<br/>(txt · md · pdf · json)"]:::io

    Loader -->|raw transcripts| P[Parser Agent]
    Loader -->|wiki / Confluence| CE[Constraint Extractor Agent]
    Loader -->|JIRA / GitHub| Mem

    P -->|topics + entities| Mem[(Shared Memory<br/>+ Audit Log)]:::mem
    CE -->|architecture rules| Mem

    Mem -->|context| SW[Story Writer Agent]
    SW -->|user stories + AC| Mem

    Mem -->|stories| ED[Epic Decomposer Agent]
    ED -->|epics → stories → tasks| Mem

    Mem -->|stories + existing tickets| GD[Gap Detector Agent]
    GD -->|gaps · conflicts · duplicates| Mem

    Mem --> Out["Output Formatter<br/>(synthesis.json + .md)"]:::io
    Mem --> AT["Audit trail<br/>(audit_trail.md)"]:::io

    Out --> User
    AT --> User

    %% Tools (sit beside agents)
    Claude[("Anthropic Claude<br/>claude-sonnet-4-5")]:::llm
    Jira[("JIRA tool<br/>(mocked)")]:::tool
    Conf[("Confluence tool<br/>(mocked)")]:::tool
    Gh[("GitHub tool<br/>(mocked)")]:::tool

    P -.->|reason| Claude
    CE -.->|reason| Claude
    CE -.->|fetch wiki| Conf
    SW -.->|reason| Claude
    ED -.->|reason| Claude
    GD -.->|reason| Claude
    GD -.->|search tickets| Jira
    GD -.->|search issues| Gh

    classDef user fill:#fff,stroke:#444,stroke-width:2px
    classDef io fill:#e6edf7,stroke:#3a5285,stroke-width:1.5px
    classDef mem fill:#fff4d6,stroke:#c2870c,stroke-width:1.5px
    classDef llm fill:#fde2f3,stroke:#a83080,stroke-width:2px
    classDef tool fill:#e0f5e0,stroke:#3a7a3a,stroke-width:1.5px
```

## The five agents

| Agent | Single responsibility | Inputs | Outputs (written to memory) | Tools used |
|---|---|---|---|---|
| **Parser** | Extract distinct topics / entities / asks from raw transcript text | Raw transcripts (txt/md/pdf) | List of `topic` records with raw quotes | `claude_tool` |
| **Constraint Extractor** | Pull architectural constraints, integrations, and platform rules from wiki / Confluence content | Confluence-style markdown | List of `constraint` records (must / should / forbidden) | `claude_tool`, `confluence_tool` |
| **Story Writer** | Draft user stories with Given/When/Then acceptance criteria for each topic | Topics from Parser, constraints from Constraint Extractor | List of `story` records | `claude_tool` |
| **Epic Decomposer** | Group stories into epics; break each story into 3-7 concrete tasks | Stories from Story Writer | Tree of `epic → stories → tasks` | `claude_tool` |
| **Gap Detector** | Compare new stories against existing backlog and constraints to find duplicates, conflicts, and gaps | Stories + epics + constraints + existing tickets | Lists of `duplicate`, `conflict`, `gap` records with confidence | `claude_tool`, `jira_tool`, `github_tool` |

Each agent runs independently. The orchestrator chains them in this order, blocking on the previous agent's memory writes before invoking the next.

## Shared memory

A single `MemoryStore` instance is passed to every agent. Two flavors of storage:

**Vector memory** (in-process: `sentence-transformers` + numpy cosine search) — for semantic similarity:
- Existing JIRA / GitHub tickets are embedded once at the start of a run using `all-MiniLM-L6-v2`
- Embeddings are held in a numpy matrix for the lifetime of the run; cosine similarity for top-K retrieval
- The Gap Detector queries this to find candidates for each new story before LLM reranking
- ChromaDB is listed in `requirements.txt` and the `MemoryStore` interface is shaped so a Chroma-backed store can be swapped in without touching agent code — for runs where embeddings must persist across processes

**Structured KV memory** — for agent handoff:
- `topics`, `constraints`, `stories`, `epics`, `gaps`, `conflicts`, `duplicates`
- Each entry includes the agent that wrote it, a timestamp, and a reference to the source content
- Downstream agents read structured records, not raw text

Why both? Vector for fuzzy lookup (`find tickets similar to this story`), KV for explicit handoff (`give me the list of stories so I can group them into epics`).

## Audit log

Every agent emits **trace events** to an append-only audit log:

```json
{
  "timestamp": "2026-05-19T22:14:31Z",
  "agent": "story_writer",
  "event": "story_drafted",
  "story_id": "ST-04",
  "source_topic_id": "T-02",
  "prompt_excerpt": "...",
  "response_excerpt": "...",
  "tokens_used": 1487,
  "reasoning": "Source quote 'cashiers can't process returns when WiFi drops' clearly implies offline-tolerant return flow; categorized as 'pos' + 'offline-mode'."
}
```

At the end of the run, the audit log is rendered to `audit_trail.md` — a human-readable walkthrough of every decision the system made. This addresses the brief's requirement: *"Audit logs must show how conclusions were reached."*

## Why multi-agent (vs. one big prompt)

The v1 single-agent design works when:
- Input is a single source
- Output is a flat list
- Detection is duplicate-only

This system breaks all three assumptions. Three reasons multi-agent fits:

1. **One reasoning task per prompt.** Story-writing reasoning is different from gap-detection reasoning. Cramming them into one prompt degraded both (we proved this in v1).
2. **Sequencing matters.** Story Writer needs to read constraints written by Constraint Extractor. Gap Detector needs to read stories written by Story Writer. A shared memory + ordered orchestrator makes this explicit.
3. **Tool invocation is bounded per agent.** Only the Gap Detector calls JIRA/GitHub tools. Only the Constraint Extractor calls Confluence. Bounding tool access per agent makes the system safer and easier to audit.

## Why this is *bounded* multi-agent, not autonomous

This is not a free-form agent system where any agent can call any tool and the run ends when the model decides it's done. It's a **fixed pipeline of specialized agents** with deterministic ordering. The benefits:

- **Reproducible.** Same input → same agent order → comparable output.
- **Testable.** Each agent has a single mocked-Claude unit test.
- **Cost-bounded.** Each run makes exactly one Claude call per agent — five calls in the standard pipeline (one per agent). The number is bounded up front.
- **Auditable.** The audit log is a complete linear trace, not a graph of agent-calls-agent.

Autonomous agent loops (where the model decides what tool to call next) are powerful but harder to audit, harder to budget, and harder to test. This system gets the benefits of specialization without the unpredictability.

## Component table

| File | Responsibility |
|---|---|
| `src/main.py` | CLI entry; loads `.env`; parses args; calls orchestrator |
| `src/orchestrator.py` | Multi-agent coordinator; constructs memory + audit log; runs agents in order |
| `src/agents/base.py` | `Agent` base class with memory access, audit emission, and prompt-template loading. Retry logic lives in each tool (e.g. `ClaudeTool`), not in `Agent`, so tools can tune their own backoff |
| `src/agents/parser_agent.py` | Topic extraction from transcripts |
| `src/agents/constraint_agent.py` | Architecture constraint extraction from wiki |
| `src/agents/story_writer_agent.py` | User stories + acceptance criteria |
| `src/agents/epic_decomposer_agent.py` | Epic grouping + task breakdown |
| `src/agents/gap_detector_agent.py` | Duplicates / conflicts / gaps detection |
| `src/tools/claude_tool.py` | Wrapped Claude API client with retry |
| `src/tools/jira_tool.py` | Mocked JIRA ticket fetch (reads `samples/jira_backlog.json`) |
| `src/tools/confluence_tool.py` | Mocked Confluence page fetch |
| `src/tools/github_tool.py` | Mocked GitHub Issues fetch |
| `src/memory/store.py` | Vector + KV shared memory |
| `src/memory/audit_log.py` | Append-only trace event log |
| `src/input_loader.py` | Reads txt / md / pdf / json |
| `src/output_formatter.py` | Renders epic → story → task hierarchy to JSON + Markdown |

## Error handling and retries

Each agent's tool calls are wrapped in `tenacity` retry logic — exponential backoff with a cap of 3 attempts. Transient errors (rate limit, network failure) retry; deterministic errors (auth failure, bad request) fail fast.

If an individual agent fails permanently:
- Its failure is recorded in the audit log
- Downstream agents are skipped if they depend on its output
- The orchestrator returns whatever was completed before the failure

This means partial results are still useful — a Gap Detector failure still produces a synthesis with stories and epics, just no gap analysis.

## Where AI is used

The Claude API is called exactly once per agent per run — five calls total for the standard pipeline. Outside those calls, everything is deterministic Python:

- File I/O
- Vector similarity (numpy)
- Audit log writes
- Output formatting

The boundary is intentional. The model handles judgment (story shape, conflict detection); the framework handles plumbing.
