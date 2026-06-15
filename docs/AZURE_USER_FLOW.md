# Azure End-to-End User Flow

**Backlog Synthesizer — Accenture · AI-First Agentic Solutions**  
Full journey from browser to synthesized Jira backlog, running on Microsoft Azure.

---

## Overview

```mermaid
flowchart TD
    A([User opens browser]) --> B[Step 1\nMicrosoft Entra ID SSO Login]
    B --> C[Step 2\nRole-Based Access Applied]
    C --> D[Step 3\nUpload Inputs]
    D --> E[Step 4\nConfigure & Launch Synthesis]
    E --> F[Step 5\nLangGraph Pipeline Executes]
    F --> G[Step 6\nReview Synthesis Output]
    G --> H{Step 7\nGuardrail + Security Review}
    H -->|Issues found| I[Step 7a\nReview Findings & Audit Trail]
    H -->|All clear| J[Step 8\nHuman-in-the-Loop Jira Gate]
    I --> J
    J -->|Approved| K[Step 9\nPush to Jira]
    J -->|Rejected| D
    K --> L[Step 10\nSync & Monitor]
    L --> M([Done — traceable backlog in Jira])
```

---

## Step 1 — Microsoft Entra ID SSO Login

**What happens on Azure:**

```mermaid
sequenceDiagram
    participant U as User (Browser)
    participant ACA as Azure Container Apps\n(backlog-synthesizer)
    participant EID as Microsoft Entra ID\nnorthstarretailcorp.onmicrosoft.com
    participant KV as Azure Key Vault\n(kv-backlog-synth)

    U->>ACA: GET https://<app>.azurecontainerapps.io
    ACA->>ACA: No valid session cookie → redirect to /auth/login
    ACA->>EID: OAuth2 Authorization Request\n(state nonce, code_challenge)
    EID->>U: Microsoft login page
    U->>EID: Credentials (MFA if configured)
    EID->>ACA: Authorization code + state
    ACA->>EID: Token exchange (code → id_token + access_token)
    ACA->>ACA: RS256 signature verify via JWKS endpoint
    ACA->>ACA: Validate nonce (single-use, 600s TTL)
    ACA->>ACA: Extract roles from id_token claims
    ACA->>KV: Read ENTRA_CLIENT_SECRET (MSI — no credential in code)
    ACA-->>U: Set encrypted session cookie → redirect to app
```

**Azure resources involved:**
- **Azure Container Apps** — hosts `src/entra_auth.py`, handles the OAuth2 flow
- **Azure Key Vault** (`kv-backlog-synth`) — stores `ENTRA_CLIENT_SECRET`, accessed via User-Assigned Managed Identity (no credentials in code or environment)
- **Microsoft Entra ID** — issues tokens for `northstarretailcorp.onmicrosoft.com`

---

## Step 2 — Role-Based Access Applied

After token validation, the user's Entra role claims determine what they can do:

```mermaid
flowchart LR
    Token["Decoded id_token\nroles claim"]

    subgraph Roles
        Admin["admin\nFull access:\nUpload · Synthesize\nPush to Jira · View all runs"]
        Contrib["contributor\nUpload · Synthesize\nPush to Jira\nView own runs"]
        Viewer["viewer\nRead-only:\nView runs + outputs\nNo synthesis · No Jira push"]
    end

    subgraph Features
        F1["Upload inputs"]
        F2["Run synthesis"]
        F3["Push to Jira"]
        F4["View audit trail"]
        F5["View run history"]
    end

    Token --> Admin & Contrib & Viewer
    Admin --> F1 & F2 & F3 & F4 & F5
    Contrib --> F1 & F2 & F3 & F4 & F5
    Viewer --> F4 & F5
```

---

## Step 3 — Upload Inputs

The user provides three inputs through the Streamlit UI:

```mermaid
flowchart TB
    subgraph Inputs["Upload Panel (app.py)"]
        T["Customer Meeting Transcript\n.txt / .pdf / .docx\nor paste raw text"]
        W["Architecture Wiki\nConfluence page URL\nor uploaded document"]
        B["Existing Backlog\nJira project key (live fetch via MCP)\nor uploaded .csv / .json"]
    end

    subgraph Validation["Pre-flight checks"]
        V1["Token estimate\n4 chars/token heuristic\nMAX_INPUT_TOKENS_PER_RUN = 50,000"]
        V2["Rate limit check\nMAX_SYNTHESES_PER_HOUR\nMAX_SYNTHESES_PER_DAY"]
        V3["Budget reserve\nRedis Lua script\natomic: current + estimated ≤ daily limit"]
    end

    subgraph Azure["Azure Storage (Azure Files)"]
        AF1["logs/ share\n10 GB\nrun logs + audit chain"]
        AF2["outputs/ share\n50 GB\nsynthesis.json · synthesis.md\naudit_trail.md"]
    end

    Inputs --> Validation
    Validation -->|approved| Run["Proceed to synthesis"]
    Validation -->|rejected| Err["Error shown to user\n(over budget / over rate limit)"]
    Run --> AF2
```

**Where inputs are sanitized:**
- `src/security.py::InputSanitizer` — 8 prompt-injection detection rules strip or redact malicious patterns before any LLM call
- PII redaction (`strict_redact=True`) replaces email, phone, SSN, card numbers, and names with `[EMAIL_1]`, `[PHONE_1]` etc. — raw PII never reaches the LLM

---

## Step 4 — Configure & Launch Synthesis

```mermaid
flowchart LR
    subgraph Config["Synthesis Configuration"]
        M["Model selection\nper-stage overrides via\nresolved_models dict\n(default: claude-sonnet-4-5 all stages)"]
        E["Embedding duplicates\nuse_embeddings_for_duplicates\n(default: true — local, $0)"]
        L["Live sources\nLive Jira fetch toggle\nLive Confluence page toggle"]
        P["Cost preset display\nfree-tier / balanced / premium"]
    end

    subgraph AzureSecrets["Azure Key Vault (MSI injection at startup)"]
        S1["ANTHROPIC_API_KEY"]
        S2["GOOGLE_API_KEY"]
        S3["JIRA_API_TOKEN"]
        S4["GITHUB_TOKEN"]
        S5["OTEL_EXPORTER_OTLP_HEADERS"]
    end

    Config --> Launch["User clicks 'Synthesize'"]
    AzureSecrets -->|injected as env vars\nby Container Apps runtime| Launch
    Launch --> Pipeline["LangGraph pipeline starts\n(run_id = UUID)"]
```

All secrets are **injected at runtime** by Azure Container Apps from Key Vault via the User-Assigned Managed Identity — they are never stored in the Docker image or the Terraform state.

---

## Step 5 — LangGraph Pipeline Executes

This is the core AI processing step. Seven LangGraph nodes run on the Azure Container Apps instance:

```mermaid
flowchart TB
    subgraph Container["Azure Container Apps — python:3.11-slim\n1 vCPU · 2 GiB RAM"]
        direction TB

        N0["initialize\nValidate state · hydrate memory\nset resolved_models"]:::node

        subgraph Parallel["Parallel fan-out (concurrent)"]
            direction LR
            N1["parse\nParserAgent\nLLM → topics [{T-01…}]\nmax_tokens=8000"]:::node
            N2["extract_constraints\nConstraintAgent\nLLM + Confluence MCP\n→ constraints [{C-01…}]\nmax_tokens=8000"]:::node
        end

        N3["write_stories\nStoryWriterAgent\nLLM → stories [{ST-01…}]\n_attach_evidence() deterministic\n_repair_source_topic_id()\nmax_tokens=16000"]:::node

        N4["decompose_epics\nEpicDecomposerAgent\nLLM → epics [{EP-01…tasks[]}]\nmax_tokens=8000"]:::node

        N5["detect_gaps\nGapDetectorAgent\nall-MiniLM-L6-v2 cosine top-5\nLLM → conflicts + gaps\nmax_tokens=16000"]:::node

        N6["finalize\n6 guardrails · OutputScanner\nPII un-redact · SHA-256 chain\nBudget settle + refund"]:::node

        N0 --> Parallel
        Parallel --> N3 --> N4 --> N5 --> N6
    end

    subgraph AzureInfra["Azure Infrastructure (per LLM call)"]
        KV2["Key Vault\nANTHROPIC_API_KEY injected"]:::azure
        OTel["OTel → Grafana Cloud\npipeline.node.* spans\nllm.call spans"]:::azure
        Prom["Prometheus :9090\nbacklog_active_synthesis +1"]:::azure
        AuditDB["Azure Files (logs/)\naudit_chain.db\nSHA-256 hash chain"]:::azure
    end

    Container --> KV2 & OTel & Prom & AuditDB

    classDef node  fill:#14532d,stroke:#4ade80,color:#dcfce7
    classDef azure fill:#1e3a5f,stroke:#3b82f6,color:#dbeafe
```

**Token & cost controls during pipeline:**
- `MAX_INPUT_TOKENS_PER_RUN = 50,000` hard ceiling before any spend
- System prompt cached via `cache_control: {"type": "ephemeral"}` — not re-billed per agent
- Per-provider circuit breakers (`CLAUDE_CB`, `GEMINI_CB`) trip after 3 failures — pipeline degrades gracefully

---

## Step 6 — Review Synthesis Output

The Streamlit UI renders the structured output from `synthesis.json`:

```mermaid
flowchart TB
    subgraph Output["Synthesis Output Panel"]
        direction TB
        EP["Epics\nEP-01 … EP-N\nwith description + story count"]
        ST["Stories (per epic)\nST-01 … ST-N\nUser story format\nGiven/When/Then AC\nPriority + rationale\nEvidence (customer quote)"]
        TASK["Tasks (per story)\nTK-01 … TK-N\nDev task breakdown"]
        GAP["Gaps detected\nG-01 … G-N\nMissing capability areas"]
        CON["Conflicts detected\nStory ID ↔ Constraint ID\nseverity: must/should/forbidden"]
        DUP["Duplicates detected\nST-xx matches existing JIRA-nnn\ncosine similarity score"]
    end

    subgraph AzureFiles["Azure Files (outputs/ share)"]
        J2["synthesis.json\nfull structured output"]
        M2["synthesis.md\nhuman-readable markdown"]
        AT["audit_trail.md\ncollapsible reasoning chain\nevery agent decision"]
    end

    Output --> AzureFiles
    AzureFiles -->|downloadable| User["User can download\nall three artifacts"]
```

The `audit_trail.md` is the AI-specific debugging artifact — every agent decision recorded with timestamps, the full reasoning, and SHA-256 hash chain verifiable by compliance reviewers.

---

## Step 7 — Guardrail + Security Review

After synthesis, deterministic checks run automatically and results surface as UI chips:

```mermaid
flowchart LR
    subgraph Guardrails["src/guardrails.py — 6 checks"]
        G1["AC count\n2 ≤ count ≤ 7\nwarn if < 2 · info if > 7"]
        G2["GWT grammar\nAC must contain\ngiven / when / then"]
        G3["Unique titles\nno duplicate story titles\nwithin the run"]
        G4["Canonical tags\n15-tag vocabulary\nnon-canonical → info"]
        G5["Story grounding\nsource_topic_id must\npoint to real parser topic"]
        G6["Priority rationale\nhigh-priority stories\nrationale ≥ 20 chars"]
    end

    subgraph Security["src/security.py — OutputScanner"]
        S1["PII scan\nemail · phone · SSN · card\n4 patterns"]
        S2["Toxicity scan\nthreats · hate-speech · explicit\n3 patterns"]
        S3["Bias scan\ngender · age · demographic\n5 patterns"]
    end

    subgraph AlertPath["Azure alerts (src/alerts.py)"]
        AL["error-severity finding\n→ Slack / MS Teams\n/ PagerDuty webhook\n2s fire-and-forget"]
    end

    Guardrails & Security --> UI2["UI chips: error / warn / info"]
    Security -->|error severity| AL
    Guardrails & Security --> AuditLog["AuditLog entry\n(hash-chained to SQLite\non Azure Files)"]
```

---

## Step 7a — Audit Trail Review (if findings exist)

```mermaid
flowchart LR
    AT["audit_trail.md\n(Azure Files · logs/ share)"]
    subgraph Events["Key audit events"]
        E1["pipeline_start\nrun_id · user_email · model"]
        E2["parse_complete\ntopic count · token usage"]
        E3["story_evidence_attached\nstory_id · source_topic_id · quote"]
        E4["source_topic_id_repaired\ninvalid_id → nearest topic (word-overlap)"]
        E5["guardrail_findings\ncode · severity · story_id"]
        E6["security_finding\ninjection / PII / toxicity"]
        E7["pipeline_complete\ncost_usd · duration_s · story_count"]
    end

    AT --> Events
    Events --> Chain["SHA-256 hash chain\nverify_chain() — each event\nhashes over previous event hash\nSQLite: audit_chain.db"]
    Chain --> Compliance["Compliance reviewer\ncan verify no tampering\nwithout access to app"]
```

---

## Step 8 — Human-in-the-Loop Jira Gate

This is the HITL boundary — the pipeline has already run (HOTL), but writing to Jira is irreversible and requires explicit approval:

```mermaid
flowchart TD
    Review["User reviews synthesis\n(epics · stories · gaps · conflicts\naudit trail · guardrail chips)"]

    Decision{Approve?}

    Review --> Decision

    Decision -->|"Yes — push to Jira"| Gate["HITL Gate\nJira push button\nonly active for admin / contributor roles"]
    Decision -->|"No — re-run"| Restart["Re-upload inputs\nor adjust model config\nand re-synthesize"]
    Decision -->|"Partial — edit first"| Edit["Download synthesis.json\nEdit offline\nRe-upload as existing backlog"]

    Gate --> Confirm["Confirmation dialog\nshows: # epics · # stories · # tasks\ntarget project key · user email"]
    Confirm -->|Confirmed| Push["Jira push executes\n(Step 9)"]
    Confirm -->|Cancelled| Review
```

**Why HITL here:**  
A wrong Jira push creates tickets visible to the entire project team, triggers notifications, and may affect sprint planning. Reversing it (bulk-deleting JIRA tickets) is operationally disruptive — so a human gate is mandatory regardless of synthesis quality.

---

## Step 9 — Push to Jira

```mermaid
sequenceDiagram
    participant U as User
    participant APP as Azure Container Apps\n(app.py)
    participant MCP as mcp-atlassian\n(MCPJiraTool)
    participant JIRA as Jira Cloud\n(northstarretailcorp)
    participant AUDIT as AuditLog\n(Azure Files)

    U->>APP: Confirm push (role=admin/contributor)
    APP->>MCP: create_issue(Epic EP-01, project=NS)
    MCP->>JIRA: POST /rest/api/3/issue → Epic created
    JIRA-->>MCP: {id: NS-201, key: NS-201}
    MCP-->>APP: epic_jira_key=NS-201

    loop For each Story under EP-01
        APP->>MCP: create_issue(Story ST-01, parent=NS-201)
        MCP->>JIRA: POST /rest/api/3/issue → Story linked to Epic
        JIRA-->>MCP: {id: NS-202, key: NS-202}
        loop For each Task under ST-01
            APP->>MCP: create_issue(Task TK-01, parent=NS-202)
            MCP->>JIRA: POST /rest/api/3/issue → Sub-task
            JIRA-->>MCP: {id: NS-203, key: NS-203}
        end
    end

    APP->>AUDIT: Record jira_push_complete\n{epic_count, story_count, task_count,\nuser_email, run_id}
    APP-->>U: Push complete — Jira keys displayed
    APP->>APP: post_jira_push_notification()\n→ Slack / MS Teams
```

---

## Step 10 — Sync & Monitor

After the backlog is in Jira, the system stays connected for ongoing sync and observability:

```mermaid
flowchart TB
    subgraph Sync["Two-way Jira Sync"]
        SS["Sync Status button\nPulls {status, assignee,\nsprintName, priority}\nfor each pushed story"]
        UP["Status update displayed\nin synthesis panel\n(live from Jira)"]
        SS --> UP
    end

    subgraph Observability["Azure + Grafana Observability"]
        direction LR
        OT["OTel traces\n→ Grafana Tempo\npipeline.run spans\nper-agent durations"]
        PM["Prometheus metrics\n→ Grafana Mimir\nbacklog_synthesis_cost_usd\nbacklog_llm_errors_total\nbacklog_active_synthesis"]
        AL2["Alerts\n→ Slack / Teams / PagerDuty\ncircuit breaker OPEN\nerror-severity security findings\npipeline failures"]
        RH["Run History panel\nper-user cost + duration\ndownload past outputs\nreplay audit trail"]
    end

    subgraph AzureInfra2["Azure Infrastructure (always-on)"]
        AF3["Azure Files\noutputs/ · logs/ shares\nper-run artifacts retained"]
        LAW["Log Analytics Workspace\ncontainer stdout/stderr\nresource metrics"]
        ACR2["Azure Container Registry\nimage: backlog-synthesizer:<sha>\nnew SHA on every main push"]
    end

    Sync & Observability --> AzureInfra2
```

---

## End-to-End on Azure — One-page Summary

```mermaid
flowchart LR
    U(["fa:fa-user User\n(northstarretailcorp)"])

    subgraph AzureCloud["Microsoft Azure — eastus"]
        EID2["Entra ID\nOAuth2/OIDC\nRole claims"]:::auth
        ACA2["Container Apps\napp.py · pipeline.py\n1 vCPU · 2 GiB"]:::app
        KV2["Key Vault\n9 secrets\nMSI access"]:::secret
        AF2["Azure Files\nlogs/ · outputs/\nSQLite audit DB"]:::storage
        ACR2["Container Registry\nbacklogsynth.azurecr.io\nGitHub SHA tag"]:::registry
        GHA2["GitHub Actions\nci.yml · deploy.yml\nterraform.yml"]:::ci
        TF2["Terraform\ninfra/terraform/\nazurerm ~3.100"]:::iac
    end

    subgraph ExternalSvc["External Services"]
        Claude2["Anthropic Claude\nSonnet 4.5"]:::llm
        Jira2["Jira Cloud\n127 tickets"]:::ext
        Conf2["Confluence\nArch wiki"]:::ext
        Grafana2["Grafana Cloud\nTraces · Metrics"]:::obs
    end

    U <-->|"① OIDC login"| EID2
    U -->|"② HTTPS"| ACA2
    ACA2 <-->|"③ secrets (MSI)"| KV2
    ACA2 <-->|"④ logs + outputs"| AF2
    ACA2 <-->|"⑤ LLM calls"| Claude2
    ACA2 <-->|"⑥ Jira MCP"| Jira2
    ACA2 <-->|"⑦ Confluence MCP"| Conf2
    ACA2 -->|"⑧ OTel traces + metrics"| Grafana2
    GHA2 -->|"⑨ push image"| ACR2
    GHA2 -->|"⑩ deploy revision"| ACA2
    ACR2 --> ACA2
    TF2 -.->|"provisions"| ACA2 & KV2 & AF2 & ACR2

    classDef auth     fill:#1e1b4b,stroke:#818cf8,color:#e0e7ff
    classDef app      fill:#0f172a,stroke:#3b82f6,color:#bfdbfe
    classDef secret   fill:#7f1d1d,stroke:#f87171,color:#fee2e2
    classDef storage  fill:#1c1917,stroke:#d97706,color:#fef3c7
    classDef registry fill:#1e3a5f,stroke:#3b82f6,color:#dbeafe
    classDef ci       fill:#1e293b,stroke:#94a3b8,color:#e2e8f0
    classDef iac      fill:#1e293b,stroke:#94a3b8,color:#e2e8f0
    classDef llm      fill:#4c1d95,stroke:#a78bfa,color:#ede9fe
    classDef ext      fill:#1a1a2e,stroke:#64748b,color:#cbd5e1
    classDef obs      fill:#0d3349,stroke:#22d3ee,color:#cffafe
```

| Step | User action | Azure resource |
|---|---|---|
| ① | Open app URL | Container Apps ingress (HTTPS) |
| ② | Login with Microsoft | Entra ID OAuth2/OIDC |
| ③ | App reads secrets at startup | Key Vault via Managed Identity |
| ④ | Upload transcript / wiki / backlog | Streamlit UI on Container Apps |
| ⑤ | Click Synthesize | LangGraph pipeline — Container Apps CPU |
| ⑥ | Pipeline calls LLMs | Anthropic / Gemini API (external) |
| ⑦ | Pipeline fetches live Jira/Confluence | MCP tools (external) |
| ⑧ | Outputs written | Azure Files (outputs/ share) |
| ⑨ | Review + approve | Browser — Streamlit UI |
| ⑩ | Push to Jira | MCPJiraTool → Jira Cloud |
| ⑪ | Traces + metrics shipped | OTel → Grafana Cloud |
| ⑫ | New code merged → auto-deploy | GitHub Actions → ACR → Container Apps |
