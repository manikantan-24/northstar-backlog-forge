# Production Readiness

This document is the authoritative record of what has been built, what remains
open, and the honest gaps before the Backlog Synthesizer can be considered
fully production-hardened. Updated to reflect the enterprise production pass
completed in June 2026.

---

## Status summary

| Severity | Items open |
|---|---|
| P0 — must close before any production user | **0 open (all done)** |
| P1 — close in month one | **1 remaining** (output directory scoping) |
| P2 — quality of life | 3 remaining |

---

## P0 — All resolved ✅

### 1. Authentication and per-user isolation ✅
**Status:** Implemented. `streamlit-authenticator` with three roles:
- **viewer** — read results + download exports only
- **contributor** — run synthesis, edit stories, push to Jira (with approval gate)
- **admin** — full access including live Atlassian, Premium models, all history

Credentials stored as bcrypt hashes in `config/auth.yaml`. Feature flags in
`config/feature_flags.yaml` let admins configure per-role capabilities live
without code changes.

**Remaining gap:** Username/password auth only. Enterprise deployments should
replace with Azure AD / SAML via `msal-streamlit-authentication`. This is a
P1 upgrade — the 3-role RBAC structure is preserved.

### 2. Secret storage ✅
**Status:** Implemented. `src/startup_check.py` validates all required secrets
at startup and refuses to start if `ANTHROPIC_API_KEY` is missing. Partial
optional configs (e.g. one Jira var set, others missing) surface warnings.
Azure deployment reads secrets from Azure Key Vault (Terraform-provisioned).
Local dev still uses `.env`; `.env.example` documents all vars.

### 3. Output authorization ✅
**Status:** Implemented. Every run records `user_id` in `logs/runs/*.json`.
The rate limiter filters by `user_id`. History dialog loads all runs for admin,
own runs only for contributors.

**Remaining gap:** The `outputs/` directory structure is not yet scoped per
user (`outputs/<user_id>/<timestamp>/`). This is acceptable for a single-tenant
deployment; multi-tenant requires the path change. Tracked as P1.

### 4. Rate limiting + cost ceiling per user ✅
**Status:** Implemented. `src/rate_limiter.py` enforces rolling-window limits:
- Default: 10 runs/hour, $5.00/day per user
- Configurable via `RATE_LIMIT_RUNS_PER_HOUR` and `RATE_LIMIT_COST_PER_DAY`
- Live usage meter shown in the sidebar for contributors/admins

### 5. PII redaction ✅
**Status:** Implemented and default-on. For uploaded content, PII redaction
is forced on regardless of the sidebar toggle (contributors cannot disable it).
Admins can override. Strict-redact mode halts the pipeline on violation.

---

## P1 — Close in month one

### 6. Enterprise SSO (Azure AD / SAML) ✅
**Status:** Implemented. `src/entra_auth.py` fully rewritten with:
- RS256 JWT signature verification via Microsoft JWKS endpoint (PyJWT + `PyJWKClient`) — replaces the previous insecure base64-decode-only path
- Server-side state nonce store with 600-second TTL and single-use consumption (CSRF protection)
- Dynamic `_cfg()` reads env vars on every call — no stale Streamlit module-cache values
- `raise_for_status()` on token exchange — HTTP errors surface immediately
- Supports both tenant-ID and tenant-domain OIDC issuer formats
- AUTH_DISABLED misconfiguration guard in `app.py` prevents accidental bypass when Entra is also configured

The 3-role RBAC (viewer / contributor / admin) maps directly to Azure AD app roles. The `auth.yaml` fallback is preserved for local dev.

### 7. Output directory scoped per user
**Status:** `user_id` is recorded in every run log but `outputs/` is not
scoped. In multi-user production, User A can load User B's outputs.
**Path forward:** Change `outputs/<timestamp>/` to `outputs/<user_id>/<timestamp>/`
and filter `_load_run_history()` to the current user's directory.
**Effort:** Half a day.

---

## P2 — Quality of life

### 8. Audit log tamper-evidence in production (append-only cloud store)
**Status:** Implemented locally. SQLite database with SHA-256 hash chain is
written per run. `verify_chain()` detects any post-hoc modification.
**Remaining gap:** SQLite is a single-host file. In multi-replica deployments,
each instance has its own database. For full compliance-grade tamper-evidence,
events should be written to an append-only cloud store (Azure Blob with
immutable storage policy, or Sigstore Rekor).
**Effort:** 1 day.

### 9. Operational alerting
**Status:** OpenTelemetry spans are emitted per agent stage (`src/telemetry.py`).
`OTEL_ENABLED=1` is set in the Azure Container App deployment (Terraform +
GitHub Actions). A console exporter logs spans locally.
**Remaining gap:** No Grafana Cloud or Honeycomb workspace is wired up. The
spans are emitted but unobserved unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set.
**Path forward:** Create a free Grafana Cloud account, set the endpoint, add
alerting rules on error_rate > 2% and p95 latency > 120s.
**Effort:** Half a day.

### 10. ChromaDB production deployment
**Status:** ChromaDB is wired as the primary vector backend when
`USE_CHROMADB=1` is set (`src/memory/store.py`). Falls back to NPZ file cache.
**Remaining gap:** The Terraform Azure Container App mounts one Azure Files
share. ChromaDB in embedded mode is fine for single-replica; the chromadb-server
mode (separate container) is needed for true multi-replica.
**Effort:** 1 day to add a chromadb-server container to the Terraform config.

---

## What's been built (full feature inventory)

### Core pipeline
- 5-agent bounded pipeline: Parser → Constraint Extractor → Story Writer →
  Epic Decomposer → Gap Detector
- Shared MemoryStore (vector + KV) with ChromaDB option
- Append-only AuditLog with SHA-256 hash chain (tamper detection)
- Post-synthesis guardrails (6 deterministic checks, individually logged)
- Story writer auto-repair for bad source_topic_id values

### MCP integration
- **Atlassian MCP** (`mcp-atlassian`): live Jira + Confluence via official server
- **GitHub MCP** (`@modelcontextprotocol/server-github`): live GitHub Issues
- Both tools fall back to REST/fixture when MCP is unavailable
- **Backlog Synthesizer MCP server** (`mcp_server.py`): exposes the pipeline
  to Claude Desktop and other agents via 5 tools:
  `synthesize_backlog`, `preview_prompts`, `get_run_history`,
  `get_run_result`, `push_to_jira`

### Enterprise features
- 3-role RBAC with admin-configurable feature flags
- Human-in-the-loop: review panel + confirmation checkbox before Jira write-back
- Rate limiting + cost ceiling per user
- PII redaction (default-on for uploads, strict-mode halt)
- Startup secret validation
- Per-stage model override with live availability status
- Provider failover (Claude ↔ Gemini ↔ Ollama)
- Prompt caching (cache_control on system prompt)

### Observability
- Comprehensive audit trail: 26+ events per run, all logged
- OTel spans per agent stage (active in Azure deployment)
- Evaluation framework: 10 golden cases, LLM-as-judge, CI regression gate

### Deployment
- Docker image (Python 3.11, non-root, health check)
- GitHub Actions: CI (`ci.yml`) + CD (`deploy.yml`) with regression gating
- Terraform IaC: ACR, Container Apps, Key Vault, Azure Files, Service Principal
- `./start.sh`: one-command local startup (starts Ollama + venv313 + Streamlit)

---

## Accepted limitations (won't fix in v1)

- **English output only.** Prompts are English; stories are always in English
  regardless of input language.
- **Story Writer is not iterative.** One shot per story. Refinement loops
  (re-run after Gap Detector flags weak AC) are deferred to v2.
- **No two-way Jira sync.** Write-back creates issues; it doesn't reconcile
  later edits or update on re-run.
- **Compare mode UI is minimal.** The A/B comparison runs correctly in the
  orchestrator; the side-by-side story diff UI is P2.
