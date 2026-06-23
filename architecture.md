# Architecture

Enterprise production-grade multi-agent AI system for sprint backlog synthesis.  
**Accenture · AI-First Agentic Solutions**

---

## Application & AI Layer

```mermaid
flowchart TB
    Browser((Browser)):::user
    EntraID["Microsoft Entra ID\nnorthstarretailcorp.onmicrosoft.com\nadmin / contributor / viewer"]:::auth
    Browser <-->|OAuth2 / OIDC| EntraID
    Browser -->|HTTPS :8501| UI

    subgraph App["Application Layer — Streamlit"]
        UI["app.py\nRole-gated · Rate limiting\nPII redaction · HITL Jira gate"]:::app

        subgraph Pipeline["LangGraph StateGraph — 7 nodes"]
            INIT["initialize"]:::agent
            P["parse\nParserAgent"]:::agent
            CE["extract_constraints\nConstraintAgent"]:::agent
            SW["write_stories\nStoryWriterAgent\n_attach_evidence()"]:::agent
            ED["decompose_epics\nEpicDecomposerAgent"]:::agent
            GD["detect_gaps\nGapDetectorAgent"]:::agent
            FIN["finalize"]:::agent
            INIT --> P & CE
            P & CE --> SW --> ED --> GD --> FIN
        end

        Mem["MemoryStore\nKV + ChromaDB / NPZ\nall-MiniLM-L6-v2"]:::store
        Audit["AuditLog\nSHA-256 chain · SQLite"]:::store
        CB["Circuit Breaker\nper provider · 3-failure threshold"]:::store

        UI --> Pipeline
        P & CE & SW & ED & GD <--> Mem
        P & CE & SW & ED & GD --> Audit
    end

    subgraph Sec["Deterministic Security Shell"]
        IS["InputSanitizer\n8 injection rules · PII redact"]:::sec
        OS["OutputScanner\nPII · Toxicity · Bias"]:::sec
        GR["Guardrails\n6 deterministic checks"]:::sec
        BG["BudgetStore\nRedis reserve / settle"]:::sec
    end

    App --> Sec

    subgraph LLMs["LLM Providers — provider-agnostic via LangChain"]
        Claude["Anthropic Claude\nSonnet 4.5 · cache_control"]:::llm
        Gemini["Google Gemini\n2.5 Flash / Pro · free-tier"]:::llm
        Ollama["Ollama Local\n$0 / call"]:::llm
    end

    SW & GD -.->|reasoning| Claude
    P & CE & ED -.->|extraction| Gemini
    P & CE & ED -.->|local| Ollama
    CB -.-|trips on failure| Claude & Gemini

    subgraph MCP["MCP Integration Layer"]
        AtlMCP["mcp-atlassian\nMCPJiraTool · MCPConfluenceTool"]:::mcp
        GHubMCP["server-github\nMCPGithubTool · 20 issues"]:::mcp
        MCPSvr["mcp_server.py\nsynthesize_backlog · push_to_jira"]:::mcp
    end

    GD -.->|jira_search| AtlMCP
    CE -.->|confluence_get_page| AtlMCP
    GD -.->|list_issues| GHubMCP
    ExtAgent["Claude Desktop / agents"]:::user -.->|tool calls| MCPSvr
    MCPSvr -.->|runs pipeline| Pipeline

    subgraph External["External Services"]
        Jira["Jira Cloud\n127 tickets · two-way sync"]:::ext
        Confluence["Confluence Cloud\nArchitecture wiki"]:::ext
        GitHub["GitHub\nnorthstar-retail-backlog"]:::ext
        Grafana["Grafana Cloud\nTempo · Mimir · Alerts"]:::obs
    end

    AtlMCP <-->|REST| Jira & Confluence
    GHubMCP <-->|REST| GitHub
    UI -->|push / sync| Jira

    OTel["OpenTelemetry\npipeline.run · llm.call\nembedding.* · guardrail.*"]:::obs
    Prom["Prometheus :9090\nactive_synthesis · cost_usd\nllm_errors · duration"]:::obs
    App --> OTel & Prom
    OTel -->|OTLP / HTTP| Grafana

    classDef user  fill:#1e293b,stroke:#94a3b8,color:#e2e8f0
    classDef auth  fill:#1e1b4b,stroke:#818cf8,color:#e0e7ff
    classDef app   fill:#0f172a,stroke:#3b82f6,color:#bfdbfe
    classDef agent fill:#14532d,stroke:#4ade80,color:#dcfce7
    classDef llm   fill:#4c1d95,stroke:#a78bfa,color:#ede9fe
    classDef mcp   fill:#0c4a6e,stroke:#38bdf8,color:#e0f2fe
    classDef store fill:#1c1917,stroke:#d97706,color:#fef3c7
    classDef sec   fill:#7f1d1d,stroke:#f87171,color:#fee2e2
    classDef ext   fill:#1a1a2e,stroke:#64748b,color:#cbd5e1
    classDef obs   fill:#0d3349,stroke:#22d3ee,color:#cffafe
```

---

## Infrastructure & Deployment

```mermaid
flowchart TB
    Repo["GitHub Repository\nmain branch"]:::ci

    subgraph CI["Shared CI — ci.yml"]
        Tests["Unit tests\nPython 3.11 + 3.13\npytest · ruff"]:::ci
        Docker["Docker build verify\nbuildx cache"]:::ci
        Eval["Eval suite — gated\n10 golden cases · LLM-as-judge"]:::ci
        Tests --> Docker --> Eval
    end

    Repo --> CI

    subgraph Azure["Microsoft Azure — eastus"]
        GHA_AZ["deploy.yml · terraform.yml"]:::deploy
        ACR["Azure Container Registry\nbacklogsynth.azurecr.io"]:::deploy
        ACA["Azure Container Apps\nscale-to-zero · min 0 / max 3"]:::deploy
        KV["Azure Key Vault\n10 secrets · MSI access"]:::deploy
        Redis["Azure Cache for Redis\nBasic C0 · atomic budget enforce\n& LangGraph RedisSaver checkpointer"]:::deploy
        AF["Azure Files\nlogs/ 10 GB · outputs/ 50 GB"]:::deploy
        TF_AZ["Terraform infra/terraform/\nazurerm ~3.100\nState: Azure Blob Storage"]:::deploy
    end

    CI --> GHA_AZ
    GHA_AZ -->|push image| ACR --> ACA
    KV -->|MSI injection| ACA
    Redis -->|REDIS_URL via KV| ACA
    AF -->|volume mount| ACA
    TF_AZ -.->|provisions| ACR & ACA & KV & Redis & AF

    classDef ci     fill:#1e293b,stroke:#94a3b8,color:#e2e8f0
    classDef deploy fill:#1e3a5f,stroke:#3b82f6,color:#dbeafe
```

---

## Agent Pipeline Detail

```mermaid
sequenceDiagram
    participant U as User
    participant O as Orchestrator<br/>(LangGraph StateGraph)
    participant P as parse<br/>(ParserAgent)
    participant CE as extract_constraints<br/>(ConstraintAgent)
    participant SW as write_stories<br/>(StoryWriterAgent)
    participant ED as decompose_epics<br/>(EpicDecomposerAgent)
    participant GD as detect_gaps<br/>(GapDetectorAgent)
    participant J as Jira (MCP)
    participant GH as GitHub (MCP)

    U->>O: transcript + wiki + backlog (≤50k tokens)
    Note over O: PII redact — email→[EMAIL_1] etc.
    Note over O: Budget reserve (Redis Lua)
    Note over O: State Checkpoint lookup / sync (RedisSaver)
    Note over O: Dedup check via run_id

    par Parallel fan-out
        O->>P: transcript text
        P->>P: LLM call → topics [{T-01…T-N, raw_quote, theme}]
        P-->>O: topics list
    and
        O->>CE: wiki text + Confluence page (MCP)
        CE->>CE: LLM call → constraints [{C-01…, severity:must|should|forbidden}]
        CE-->>O: constraints list
    end

    Note over O: Fan-in — write_stories waits for both

    O->>SW: topics + constraints
    SW->>SW: LLM call → stories [{ST-01…, user_story, AC[]}]
    SW->>SW: _repair_source_topic_id() — snap bad IDs by word-overlap
    SW->>SW: _attach_evidence() — deterministic quote lookup (no model authorship)
    SW-->>O: stories with grounded evidence

    O->>ED: stories
    ED->>ED: LLM call → epics [{EP-01, stories:[{tasks:[TK-01…]}]}]
    ED-->>O: epic hierarchy

    O->>GD: stories + existing tickets
    GD->>J: jira_search (MCP) → live tickets
    GD->>GH: list_issues (MCP) → GitHub issues
    GD->>GD: all-MiniLM-L6-v2 → cosine top-5 candidates (threshold 0.6)
    GD->>GD: LLM call → conflicts + gaps (not duplicates — those are local)
    GD-->>O: {duplicates, conflicts, gaps}

    Note over O: 6 guardrail checks (AC count, GWT grammar,<br/>unique titles, canonical tags, story grounding, priority rationale)
    Note over O: OutputScanner — PII / toxicity / bias
    Note over O: PII un-redact (finalize node)
    Note over O: SHA-256 audit chain fingerprint
    Note over O: Budget settle + refund delta (Redis)
    O-->>U: synthesis.json + synthesis.md + audit_trail.md

    U->>U: Review guardrail chips + audit trail
    U->>J: [human approves] Push to Jira (HITL gate)
    J-->>U: Epic → Story → Sub-task created
    U->>J: Sync status from Jira
    J-->>U: {status, assignee, sprint, priority}
```

---

## Authentication & Security Layer

### Microsoft Entra ID SSO (`src/entra_auth.py`)

| Concern | Implementation |
|---|---|
| Token verification | RS256 signature via Microsoft JWKS endpoint (`PyJWKClient` + PyJWT) |
| CSRF protection | Server-side state nonce store — UUID per request, 600s TTL, single-use |
| Config freshness | `_cfg()` reads env vars dynamically on every call (no Streamlit module-cache stale values) |
| HTTP errors | `raise_for_status()` on token exchange — 4xx/5xx surfaces immediately |
| Issuer trust | Accepts both `login.microsoftonline.com/{tid}/v2.0` and `sts.windows.net/{tid}/` |
| Misconfiguration guard | Hard-fail if `AUTH_DISABLED=1` and `ENTRA_TENANT_ID` are both set |

### Jira Security (`src/tools/jira_tool.py`)

| Concern | Implementation |
|---|---|
| Project key injection | Regex `^[A-Z][A-Z0-9]{1,9}$` validated at init — raises `ToolError` on mismatch |
| JQL injection | Full escaping of `\`, `"`, `'` in all search strings before JQL interpolation |

---

## Security & Data Flow

```mermaid
flowchart LR
    T["Raw transcript\n(may contain PII)"]
    R["InputSanitizer\n8 injection rules\nemail→[EMAIL_1]\nphone→[PHONE_1]\nSSN→[SSN_1]\ncard→[CARD_1]\nname→[NAME_1]"]
    LLM["LLM APIs\nNever sees raw PII\nOnly [EMAIL_x] tokens"]
    OS["OutputScanner\nPII · Toxicity\nDemographic Bias"]
    GR["Guardrails (6)\nAC grammar · grounding\ntag vocab · priority"]
    A["AuditLog\nSHA-256 hash chain\nSQLite · tamper-evident\nredacted form only"]
    U["User output\nPII restored\n[EMAIL_1]→original"]
    J["Jira\nHuman approval\nrequired before write\n(HITL gate)"]

    T --> R
    R -->|redacted text| LLM
    R -->|redacted excerpts| A
    LLM --> OS --> GR --> U
    GR --> A
    U -->|un-redacted output| U
    U -.->|human approves| J

    style R fill:#7f1d1d,color:#fecaca
    style OS fill:#7c2d12,color:#fed7aa
    style GR fill:#713f12,color:#fef9c3
    style A fill:#1c1917,color:#fef3c7
    style J fill:#1e3a5f,color:#dbeafe
```

### Azure resource summary

| Resource | Detail |
|---|---|
| Container Registry | Azure Container Registry — `backlogsynth.azurecr.io` |
| Container Runtime | Azure Container Apps — scale-to-zero, min 0 / max 3 replicas |
| Secret Storage | Azure Key Vault — 10 secrets, User-Assigned Managed Identity (MSI) |
| Budget & State Checkpoints | Azure Cache for Redis — Basic C0, atomic Lua reserve/settle & RedisSaver checkpointer |
| Persistent Storage | Azure Files — `logs/` 10 GB + `outputs/` 50 GB (SMB share) |
| Logging | Log Analytics Workspace — 30-day retention |
| IaC state backend | Azure Blob Storage (`stbacklogstate`) |
| IaC provider | `azurerm ~3.100` |
| Autoscaling | Container Apps built-in — min 0 / max 3 replicas |
| Rollback | Revision traffic weights — instant cutover to prior revision |

---

## Evaluation & Quality Harness

```mermaid
flowchart LR
    GD["Golden Dataset\n10 cases\nevaluation/golden_dataset/"]:::store
    RUN["run_evaluation.py\n--use-llm-judge"]:::app
    DET["6 Deterministic Metrics\nstory_count_in_range\nac_well_formed\nrequired_topics_present\nforbidden_topics_absent\nexpected_duplicates_found\nexpected_conflicts_found"]:::agent
    JDG["LLM-as-Judge (Claude)\n5 dimensions\nac_quality · priority_justification\nstory_granularity · tag_accuracy\nconflict_reasoning\nscored 1–5 → normalised [0,1]"]:::llm
    DASH["dashboard.py\n--fail-on-regression\n--regression-threshold 0.10"]:::deploy
    CI["CI gate\neval-suite job\nfails PR if score drops ≥0.10"]:::deploy

    GD --> RUN
    RUN --> DET & JDG
    DET & JDG --> DASH --> CI

    classDef store  fill:#1c1917,stroke:#d97706,color:#fef3c7
    classDef app    fill:#0f172a,stroke:#3b82f6,color:#bfdbfe
    classDef agent  fill:#14532d,stroke:#4ade80,color:#dcfce7
    classDef llm    fill:#4c1d95,stroke:#a78bfa,color:#ede9fe
    classDef deploy fill:#1e3a5f,stroke:#3b82f6,color:#dbeafe
```
