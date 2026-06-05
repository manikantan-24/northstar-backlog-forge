# Architecture

Enterprise production-grade multi-agent AI system for sprint backlog synthesis.  
**Accenture · AI-First Agentic Solutions**

---

## System Architecture

```mermaid
flowchart TB
    %% ── User & Identity ──────────────────────────────────────────
    Browser((Browser)):::user
    EntraID["Microsoft Entra ID\nnorthstarretailcorp.onmicrosoft.com\nadmin / contributor / viewer"]:::auth

    Browser <-->|OAuth2 / OIDC| EntraID

    %% ── Application ──────────────────────────────────────────────
    Browser -->|HTTPS| App

    subgraph App["Application Layer — Streamlit"]
        UI["app.py\nRole-gated UI · Feature flags\nRate limiting · PII redaction\nHuman-in-the-loop Jira gate\nExpandable profile panel"]:::app

        subgraph Orch["Orchestrator (orchestrator.py)"]
            P["Parser\nAgent"]:::agent
            CE["Constraint\nExtractor"]:::agent
            SW["Story\nWriter"]:::agent
            ED["Epic\nDecomposer"]:::agent
            GD["Gap\nDetector"]:::agent
            P --> CE --> SW --> ED --> GD
        end

        Mem["MemoryStore\nKV + Vector\nChromaDB"]:::store
        Audit["AuditLog\nSHA-256 hash chain\nSQLite · 26+ events/run"]:::store

        UI --> Orch
        P & CE & SW & ED & GD <--> Mem
        P & CE & SW & ED & GD --> Audit
    end

    %% ── LLM Providers ─────────────────────────────────────────────
    subgraph LLMs["LLM Providers"]
        Claude["Anthropic Claude\nSonnet 4.5\n+ cache_control"]:::llm
        Gemini["Google Gemini\n2.5 Flash / Pro\n+ JSON mode"]:::llm
        Ollama["Ollama Local\nllama3.2:3b\n+ format:json"]:::llm
    end

    SW & GD -.->|reasoning| Claude
    P & CE & ED -.->|extraction| Gemini
    P & CE & ED -.->|local free| Ollama

    %% ── MCP Layer ─────────────────────────────────────────────────
    subgraph MCP["MCP Integration Layer"]
        AtlMCP["mcp-atlassian\nMCPJiraTool\nMCPConfluenceTool"]:::mcp
        GHubMCP["server-github\nMCPGithubTool\n20 issues live"]:::mcp
        MCPSvr["mcp_server.py\nsynthesize_backlog\npreview_prompts\nget_run_history\npush_to_jira"]:::mcp
    end

    GD -.->|jira_search| AtlMCP
    CE -.->|confluence_get_page| AtlMCP
    GD -.->|list_issues| GHubMCP
    ExtAgent["Claude Desktop\nor other agents"]:::user -.->|tool calls| MCPSvr
    MCPSvr -.->|runs pipeline| Orch

    %% ── External Services ─────────────────────────────────────────
    subgraph External["External Services"]
        Jira["Jira Cloud\nNS project\n127 tickets live\ntwo-way sync"]:::ext
        Confluence["Confluence Cloud\nArchitecture wiki"]:::ext
        GitHub["GitHub\nnorthstar-retail-backlog\n20 seeded issues"]:::ext
        Grafana["Grafana Cloud\nTempo — traces\nMimir — metrics\nAlerting rules"]:::obs
    end

    AtlMCP <-->|REST| Jira
    AtlMCP <-->|REST| Confluence
    GHubMCP <-->|REST| GitHub
    UI -->|Push synthesis| Jira
    UI -->|Sync status| Jira

    %% ── Observability ─────────────────────────────────────────────
    OTel["OpenTelemetry\npipeline.run\nstage.* spans\nllm.call spans\ntool.* spans\nguardrail.* spans"]:::obs
    App --> OTel
    OTel -->|OTLP / HTTP| Grafana

    %% ── Deployment ────────────────────────────────────────────────
    subgraph Deploy["Deployment (Azure)"]
        GHA["GitHub Actions\nCI: 210 tests + lint\nCD: build→push→deploy"]:::deploy
        ACR["Azure Container\nRegistry"]:::deploy
        ACA["Azure Container Apps\npython:3.11-slim\nscale-to-zero"]:::deploy
        KV["Azure Key Vault\nAnthropicKey\nJiraToken\nGoogleKey"]:::deploy
        AF["Azure Files\nlogs/ outputs/\nper-user scoped"]:::deploy
        TF["Terraform IaC\nmain.tf · variables.tf\noutputs.tf"]:::deploy
    end

    GHA -->|push image| ACR
    GHA -->|deploy| ACA
    ACR --> ACA
    KV --> ACA
    AF --> ACA
    TF -.->|provisions| ACR & ACA & KV & AF

    App -.- ACA

    %% ── Styles ────────────────────────────────────────────────────
    classDef user     fill:#1e293b,stroke:#94a3b8,color:#e2e8f0
    classDef auth     fill:#1e1b4b,stroke:#818cf8,color:#e0e7ff
    classDef app      fill:#0f172a,stroke:#3b82f6,color:#bfdbfe
    classDef agent    fill:#14532d,stroke:#4ade80,color:#dcfce7
    classDef llm      fill:#4c1d95,stroke:#a78bfa,color:#ede9fe
    classDef mcp      fill:#0c4a6e,stroke:#38bdf8,color:#e0f2fe
    classDef store    fill:#1c1917,stroke:#d97706,color:#fef3c7
    classDef ext      fill:#1a1a2e,stroke:#64748b,color:#cbd5e1
    classDef obs      fill:#0d3349,stroke:#22d3ee,color:#cffafe
    classDef deploy   fill:#1e3a5f,stroke:#3b82f6,color:#dbeafe
```

---

## Agent Pipeline Detail

```mermaid
sequenceDiagram
    participant U as User
    participant O as Orchestrator
    participant P as Parser
    participant CE as Constraint Extractor
    participant SW as Story Writer
    participant ED as Epic Decomposer
    participant GD as Gap Detector
    participant J as Jira (MCP)
    participant GH as GitHub (MCP)

    U->>O: transcript + wiki + backlog
    Note over O: PII redaction (email→[EMAIL_1] etc.)
    O->>P: transcript text
    P->>P: Gemini Flash call → topics
    P-->>O: [{id:T-01, theme, quote, speaker}]

    O->>CE: wiki text + Confluence page (MCP)
    CE->>CE: Gemini Flash call → constraints
    CE-->>O: [{id:C-01, severity:must, statement}]

    O->>SW: topics + constraints
    SW->>SW: Claude Sonnet → stories + AC
    SW->>SW: auto-repair bad source_topic_ids
    SW-->>O: [{id:ST-01, title, user_story, AC[]}]

    O->>ED: stories
    ED->>ED: Gemini Flash → epics + tasks
    ED-->>O: [{epic, stories:[{tasks:[]}]}]

    O->>GD: stories + tickets
    GD->>J: jira_search (MCP) → 50 tickets
    GD->>GH: list_issues (MCP) → 20 issues
    GD->>GD: sentence-transformers → duplicates
    GD->>GD: Claude Sonnet → conflicts + gaps
    GD-->>O: {duplicates, conflicts, gaps}

    Note over O: 6 guardrail checks (AC, grounding, tags...)
    Note over O: PII un-redact output only
    Note over O: Audit chain fingerprint (SHA-256)
    O-->>U: synthesis result

    U->>J: [human approves] Push to Jira
    J-->>U: Epic→Story→Sub-task created
    U->>J: Sync status from Jira
    J-->>U: {status, assignee, priority}
```

---

## Security & Data Flow

```mermaid
flowchart LR
    T["Raw transcript\n(may contain PII)"]
    R["Redactor\nemail→[EMAIL_1]\nphone→[PHONE_1]\nSSN→[SSN_1]\ncard→[CARD_1]\nname→[NAME_1]"]
    LLM["LLM APIs\nNever sees raw PII\nOnly [EMAIL_1] tokens"]
    A["Audit Log\nSHA-256 hash chain\nRedacted form kept\nTamper-evident"]
    U["User output\nPII restored\n[EMAIL_1]→original"]
    J["Jira\nHuman approval\nrequired before write"]

    T --> R
    R -->|redacted text| LLM
    R -->|redacted excerpts| A
    LLM -->|synthesis| U
    U -->|un-redacted| U
    U -.->|human approves| J

    style R fill:#7f1d1d,color:#fecaca
    style A fill:#1c1917,color:#fef3c7
    style J fill:#1e3a5f,color:#dbeafe
```
