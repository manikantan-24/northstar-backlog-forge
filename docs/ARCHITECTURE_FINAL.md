# Backlog Synthesizer — Final End-to-End Architecture

> Enterprise production-grade multi-agent AI system  
> Accenture · AI-First Agentic Solutions · June 2026

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           USERS & IDENTITY                                   │
│                                                                               │
│   ┌──────────┐   Entra ID SSO    ┌──────────────────────────────────────┐   │
│   │ Browser  │ ◄──────────────── │  Microsoft Entra ID                  │   │
│   │          │    OAuth2 / OIDC  │  northstarretailcorp.onmicrosoft.com  │   │
│   └────┬─────┘                   │  3 Roles: admin / contributor / viewer│   │
│        │                         └──────────────────────────────────────┘   │
└────────┼────────────────────────────────────────────────────────────────────┘
         │ HTTPS
┌────────▼────────────────────────────────────────────────────────────────────┐
│                        APPLICATION LAYER (Streamlit)                         │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  app.py — Enterprise UI                                              │    │
│  │  • Role-gated sidebar (viewer/contributor/admin)                     │    │
│  │  • Feature flags  • Rate limiting  • PII redaction toggle            │    │
│  │  • Live pipeline log  • Expandable profile panel                     │    │
│  │  • Human-in-the-loop Jira approval gate                              │    │
│  │  • Admin Settings panel  • Per-stage model override                  │    │
│  └────────────────────────────┬────────────────────────────────────────┘    │
│                                │                                              │
│  ┌─────────────────────────────▼──────────────────────────────────────┐     │
│  │  src/orchestrator.py — Pipeline Coordinator                         │     │
│  │                                                                      │     │
│  │  ┌──────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │     │
│  │  │ Parser   │→ │ Constraint  │→ │   Story     │→ │   Epic      │  │     │
│  │  │ Agent    │  │ Extractor   │  │   Writer    │  │ Decomposer  │  │     │
│  │  │          │  │ Agent       │  │   Agent     │  │   Agent     │  │     │
│  │  └──────────┘  └─────────────┘  └─────────────┘  └──────┬──────┘  │     │
│  │       ↓              ↓                 ↓                  ↓         │     │
│  │  topics         constraints        stories           epics+tasks    │     │
│  │       └──────────────┴─────────────────┴──────────────────┘         │     │
│  │                               ↓                                      │     │
│  │                    ┌──────────▼──────────┐                           │     │
│  │                    │   Gap Detector       │                           │     │
│  │                    │   Agent              │                           │     │
│  │                    │  (embeddings+LLM)    │                           │     │
│  │                    └──────────────────────┘                           │     │
│  │                    duplicates · conflicts · gaps                      │     │
│  └─────────────────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Full Component Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           LLM PROVIDERS                                       │
│                                                                               │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │  Anthropic       │  │  Google Gemini   │  │  Ollama (Local)          │   │
│  │  Claude Sonnet   │  │  2.5 Flash / Pro │  │  llama3.2:3b             │   │
│  │  claude_tool.py  │  │  gemini_tool.py  │  │  ollama_tool.py          │   │
│  │  + cache_control │  │  + JSON mode     │  │  + format:json           │   │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────┘   │
│         ↑ OTel llm.call spans track every LLM invocation with token counts   │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                        MCP INTEGRATION LAYER                                  │
│                                                                               │
│  CONSUMING MCP SERVERS                                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  mcp-atlassian (Python, pip installable)                              │    │
│  │  • MCPJiraTool  →  jira_search, jira_create_issue                   │    │
│  │  • MCPConfluenceTool  →  confluence_get_page, confluence_search      │    │
│  │  Fallback: JiraTool (REST) / ConfluenceTool (REST) if MCP down       │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  @modelcontextprotocol/server-github (npx, Node.js)                  │    │
│  │  • MCPGithubTool  →  list_issues, search_issues                     │    │
│  │  Fallback: GithubTool (fixture) if MCP down                          │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                               │
│  EXPOSING AS MCP SERVER                                                       │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  mcp_server.py  (FastMCP, stdio transport)                           │    │
│  │  • synthesize_backlog   • preview_prompts                            │    │
│  │  • get_run_history      • get_run_result   • push_to_jira            │    │
│  │  → Callable from Claude Desktop, other agents, Claude.ai            │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                    EXTERNAL INTEGRATIONS                                       │
│                                                                               │
│  ┌──────────────────────────┐    ┌──────────────────────────────────────┐   │
│  │  Atlassian Cloud          │    │  GitHub                               │   │
│  │  Jira (NS project)        │    │  northstar-retail-backlog             │   │
│  │  • 127 live tickets       │    │  • 20 seeded issues                   │   │
│  │  • Write-back: Epic→Story │    │  • Duplicate detection                │   │
│  │  • Two-way sync (status)  │    └──────────────────────────────────────┘   │
│  │  Confluence               │    ┌──────────────────────────────────────┐   │
│  │  • Architecture wiki      │    │  Grafana Cloud (ap-south-1)           │   │
│  └──────────────────────────┘    │  • Tempo: distributed traces          │   │
│                                   │  • Mimir: metrics (counters, histos)  │   │
│                                   │  • Alerting: error rate, latency, SLO │   │
│                                   └──────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                    MEMORY & AUDIT LAYER                                        │
│                                                                               │
│  ┌────────────────────────────┐    ┌──────────────────────────────────────┐  │
│  │  MemoryStore               │    │  AuditLog                             │  │
│  │  • KV: topics/constraints/ │    │  • 26+ events per run                 │  │
│  │    stories/epics/gaps       │    │  • SHA-256 hash chain                 │  │
│  │  • Vector: sentence-xformers│    │  • SQLite persistence                 │  │
│  │  • ChromaDB (persistent)   │    │  • verify_chain() tamper detection    │  │
│  └────────────────────────────┘    │  • PII stays redacted in audit        │  │
│                                    └──────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                    ENTERPRISE FEATURES                                         │
│                                                                               │
│  Authentication        Rate Limiting        Feature Flags                    │
│  • Entra ID SSO        • 10 runs/hr         • Per-role capabilities          │
│  • 3-role RBAC         • $5/day ceiling     • Stage model locks               │
│  • OAuth2 PKCE         • Per-user scoped    • Live admin edit                 │
│                                                                               │
│  PII Redaction         Guardrails           Human-in-the-loop                │
│  • Default ON uploads  • 6 post-synthesis   • Jira approval gate             │
│  • Strict halt mode    • AC count/grammar   • Review panel                   │
│  • Stable tokens       • Story grounding    • Confirmation checkbox           │
│  • Audit stays redacted• Tag vocabulary                                       │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                    OBSERVABILITY                                               │
│                                                                               │
│  OpenTelemetry (OTEL_ENABLED=1)                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  pipeline.run  ──────────────────────────────────────  28s total    │    │
│  │    stage.parser              llm.call [gemini, 1240→620]    4.1s    │    │
│  │    stage.constraint          llm.call [gemini, 980→490]     5.2s    │    │
│  │    stage.story_writer        llm.call [claude, 3400→1900]  11.8s    │    │
│  │    stage.epic_decomposer     llm.call [gemini, 2100→980]    4.9s    │    │
│  │    stage.gap_detector        tool.jira_search [MCP]  0.8s          │    │
│  │                              tool.github_list [MCP]  0.6s          │    │
│  │                              embedding.index [50 tickets]  0.3s    │    │
│  │                              llm.call [claude, 2800→1200]  0.7s    │    │
│  │    guardrail.ac_count  guardrail.story_grounding  guardrail.tags    │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│  → Grafana Cloud: Tempo (traces) + Mimir (metrics) + Alerting rules         │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                    DEPLOYMENT PIPELINE                                         │
│                                                                               │
│  Developer → git push → GitHub Actions                                        │
│                              │                                                │
│              ┌───────────────▼──────────────┐                                │
│              │  CI: ci.yml                  │                                │
│              │  • pytest (210 tests)        │                                │
│              │  • ruff lint                 │                                │
│              │  • docker build verify       │                                │
│              │  • eval regression gate      │                                │
│              └───────────────┬──────────────┘                                │
│                               │ on main merge                                 │
│              ┌───────────────▼──────────────┐                                │
│              │  CD: deploy.yml              │                                │
│              │  • Build Docker image        │                                │
│              │  • Push to ACR               │                                │
│              │  • az containerapp update    │                                │
│              │  • Health check              │                                │
│              └───────────────┬──────────────┘                                │
│                               │                                               │
│              ┌───────────────▼──────────────┐                                │
│              │  Azure Container Apps        │                                │
│              │  • python:3.11-slim Docker   │                                │
│              │  • Scale to zero (idle=$0)   │                                │
│              │  • Azure Files (logs/outputs)│                                │
│              │  • Key Vault (secrets)       │                                │
│              │  • OTEL_ENABLED=1            │                                │
│              └──────────────────────────────┘                                │
│                                                                               │
│  IaC: infra/terraform/  (main.tf, variables.tf, outputs.tf)                 │
│  • ACR · Container Apps · Key Vault · Azure Files · Service Principal        │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow: One Pipeline Run

```
User uploads transcript.txt + architecture.md
           │
           ▼
    [PII Redaction]  ──── Audit: pii_redacted {EMAIL:1, PHONE:1, NAME:2}
    j.smith@co.com → [EMAIL_1]
           │
           ▼
    ┌─ Parser Agent ─────────────────────────────────────────────────────┐
    │  Prompt → Gemini Flash                                              │
    │  Output → topics: [{id:"T-01", theme:"offline POS", quote:"..."}]  │
    │  OTel → llm.call span (1240 in / 620 out tokens)                   │
    └────────────────────────────────────────────────────────────────────┘
           │ topics
           ▼
    ┌─ Constraint Extractor ─────────────────────────────────────────────┐
    │  Prompt → Gemini Flash + Confluence wiki via Atlassian MCP          │
    │  Output → constraints: [{id:"C-01", severity:"must", ...}]          │
    │  OTel → llm.call + tool.confluence_get_page spans                   │
    └────────────────────────────────────────────────────────────────────┘
           │ topics + constraints
           ▼
    ┌─ Story Writer Agent ───────────────────────────────────────────────┐
    │  Prompt → Claude Sonnet (best reasoning for story quality)          │
    │  Auto-repair: "..." source_topic_id → T-01 by word overlap          │
    │  Evidence: raw_quote from topic attached (never LLM-generated)      │
    │  Output → 12 user stories with Given/When/Then AC                   │
    └────────────────────────────────────────────────────────────────────┘
           │ stories
           ▼
    ┌─ Epic Decomposer ──────────────────────────────────────────────────┐
    │  Prompt → Gemini Flash                                              │
    │  Output → 3 epics × 4 stories × 5 tasks = full hierarchy           │
    └────────────────────────────────────────────────────────────────────┘
           │ stories + 127 Jira tickets (MCP) + 20 GitHub issues (MCP)
           ▼
    ┌─ Gap Detector ─────────────────────────────────────────────────────┐
    │  Embeddings (sentence-transformers) → 4 duplicates detected         │
    │  LLM (Claude Sonnet) → 2 conflicts, 3 gaps                          │
    │  OTel → embedding.index + tool.jira_search + llm.call spans         │
    └────────────────────────────────────────────────────────────────────┘
           │
           ▼
    [6 Guardrail Checks]  ──── Audit: each finding individually logged
           │
           ▼
    [PII Un-redact]  ──── [EMAIL_1] → j.smith@co.com (output only)
           │
           ▼
    Audit trail: SHA-256 hash chain over all 26+ events
    Result: epics + stories + gaps + conflicts + duplicates
           │
           ├── Display in Streamlit UI
           ├── Save to outputs/<user_id>/<timestamp>/synthesis.json
           └── [Human approves] → Push to Jira as Epic→Story→Sub-task
                                  Two-way sync reads back status
```

---

## Technology Stack

| Layer | Technology |
|---|---|
| **UI** | Streamlit 1.58, Python 3.13 |
| **Auth** | Microsoft Entra ID (OAuth2), streamlit-authenticator (fallback) |
| **Agents** | Custom Python agents, bounded pipeline |
| **LLMs** | Anthropic Claude Sonnet 4.5, Google Gemini 2.5 Flash, Ollama llama3.2:3b |
| **MCP** | mcp-atlassian 0.21, @modelcontextprotocol/server-github, FastMCP |
| **Memory** | sentence-transformers (all-MiniLM-L6-v2), ChromaDB, SQLite |
| **Observability** | OpenTelemetry SDK, Grafana Cloud (Tempo + Mimir) |
| **Deployment** | Docker (python:3.11-slim), GitHub Actions, Terraform, Azure Container Apps |
| **Testing** | pytest 9.0, 210 tests, 100% pass |

---

## Security & Compliance

| Control | Implementation |
|---|---|
| **Identity** | Microsoft Entra ID SSO, role-based access |
| **Data protection** | PII redaction at trust boundary (email, phone, SSN, card, name) |
| **Audit integrity** | SHA-256 hash chain, tamper-evident SQLite log |
| **Secrets** | Azure Key Vault (production), startup validation (refuses to start without required vars) |
| **Write protection** | Human-in-the-loop approval before any Jira write |
| **Rate limiting** | 10 runs/hr, $5/day per user |
| **Data isolation** | User-scoped output directories and run history |
