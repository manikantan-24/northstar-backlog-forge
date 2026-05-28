# Production Readiness

This document is an honest assessment of what's missing before the Backlog Synthesizer could host real customer data on production infrastructure. The system works end-to-end for demos and is structured so the gaps below are additive rather than rewrites, but they should be closed before a single external user touches it.

Gaps are grouped by severity:

- **P0** — must close before any production user
- **P1** — close in the first month after launch
- **P2** — quality-of-life and operability improvements

Each item carries a rough effort estimate so the roadmap is plannable.

---

## P0 — blockers for any production user

### 1. Authentication and per-user isolation
**Status:** absent. Anyone who reaches the Streamlit URL can run the pipeline and read every saved run.
**Why it matters:** transcripts and architecture wikis often contain sensitive product strategy. The current single-tenant single-user assumption breaks the moment a second person logs in.
**Path forward:** wrap the app in `streamlit-authenticator` with two roles — `viewer` (run + read own outputs) and `admin` (full feature surface). Outputs/, logs/ and the `.cache/memory/kv/` directory must be scoped per `account_id`. Drop a `config/auth.yaml` and gate every dialog/expander behind `is_admin()`.
**Effort:** 1-2 days.

### 2. Secret storage
**Status:** `.env` file at the repo root holds the Anthropic key, Jira API token, and Google key in cleartext. It is gitignored, but it is also fully readable by anyone on the host.
**Why it matters:** an attacker who lands on the host (or a teammate browsing the home directory) sees production credentials. Token rotation is manual.
**Path forward:** read every secret from an environment variable that the deployment platform sets (Fly.io secrets, AWS Secrets Manager, GCP Secret Manager). Refuse to start if any required secret is unset. Keep `.env.example` as documentation only.
**Effort:** half a day plus deployment wiring.

### 3. Output authorization
**Status:** `outputs/<timestamp>/` directories are world-readable by Streamlit, regardless of who initiated the run.
**Why it matters:** the run history dialog will happily surface another user's synthesis once auth is added.
**Path forward:** tie output paths to `account_id`, e.g. `outputs/<account_id>/<timestamp>/`. The history loader must filter by the current user's id before iterating.
**Effort:** half a day, paired with item 1.

### 4. Rate limiting + cost ceiling per user
**Status:** none. A user (or a malicious one) can submit unlimited synthesis runs.
**Why it matters:** each run is 3-5 Claude calls (~$0.05 on Sonnet 4.5). A loop at ten runs/minute is $30/hour of unauthorised spend.
**Path forward:** rolling-window counter keyed by `account_id` enforcing N runs per hour and a soft USD ceiling per day. The orchestrator already records `token_usage` and cost in `logs/runs/*.json`; the gate just sums recent runs.
**Effort:** half a day.

### 5. PII redaction made non-optional for tenant-untrusted input
**Status:** redaction is opt-in via the sidebar toggle. The strict-redact halt-on-violation mode is wired but not enforced by default.
**Why it matters:** if a user pastes a customer transcript, the LLM sees raw PII unless they remember to flip the switch.
**Path forward:** flip the default. When the request is flagged as "untrusted upload," force `redact_pii=True` and `strict_redact=True`. Admins can override; viewers cannot.
**Effort:** half a day.

---

## P1 — close in month one

### 6. Audit log durability and tamper-evidence
**Status:** audit trail is written to `audit_trail.md` in each output directory. Anyone with filesystem access can edit it after the fact.
**Why it matters:** the audit log is the "show your reasoning" evidence reviewers ask for. Tamper-evident storage is the difference between a useful artefact and a defensible one.
**Path forward:** also write events to an append-only structured log (SQLite + WAL, or Postgres with a hash chain like Sigstore Rekor). Periodically sign + upload to immutable storage (S3 with object lock).
**Effort:** 1-2 days.

### 7. Observability — metrics, traces, alerting
**Status:** the application writes to a local logger. No metrics, no traces, no alerts.
**Why it matters:** when a tenant reports "the synthesis came back empty," you have no signal short of grep on the run logs.
**Path forward:** emit OpenTelemetry spans per agent stage. Forward to Grafana Cloud or Honeycomb. Alert on (a) error rate >2% per 15-minute window, (b) p95 stage latency >120 s, (c) cost ceiling breaches.
**Effort:** 2-3 days.

### 8. Embedding model cold-start cost
**Status:** sentence-transformers downloads ~80 MB on first use. The Streamlit UI shows a spinner; the CLI just hangs silently.
**Why it matters:** in a fresh container, the first synthesis with a >20-ticket backlog is 30-60 seconds slower than every subsequent run.
**Path forward:** bake the model into the container image, or pre-warm at startup with a side-loaded init container.
**Effort:** half a day (Dockerfile change).

### 9. LLM provider failover
**Status:** Anthropic is the default; Gemini is supported but selecting it is a manual switch.
**Why it matters:** Anthropic going down (or being rate-limited) takes the pipeline down with it.
**Path forward:** wrap `_build_tool_for_model` with a retry-then-fallback policy — on `ToolError` after N retries, automatically retry the stage on the alternate provider. Log both attempts to the audit trail.
**Effort:** 1 day.

### 10. CI integration with the evaluation harness
**Status:** the GitHub Actions workflow runs unit tests and (optionally, gated) the evaluation suite against live Claude. The dashboard step is wired but no thresholds are enforced.
**Why it matters:** prompt regressions can silently degrade output quality. The dashboard surfaces them retrospectively; CI should block the PR.
**Path forward:** in `evaluation/dashboard.py`, exit non-zero when any case's deterministic score drops ≥0.10 vs. the previous run on main. Wire the same gate into the workflow on PRs targeting main.
**Effort:** 2 hours.

---

## P2 — quality of life and operability

### 11. Persistent vector store using ChromaDB
**Status:** `MemoryStore` persists embeddings to disk as `.npz` files (`MEMORY_PERSISTENT=1`). Works, but doesn't scale beyond a single host.
**Why it matters:** multi-replica deployments will re-embed the same backlog on every cold start.
**Path forward:** swap the persistent layer behind the existing `index_tickets()` interface for ChromaDB (which is already in `requirements.txt`). Keep the file-based fallback for local dev.
**Effort:** 1 day.

### 12. UI polish for failure cases
**Status:** when an agent fails, the UI shows a single red error line. The five-stage timeline doesn't visually distinguish "skipped because upstream failed" from "skipped because no input."
**Path forward:** different styling for the two skipped states; an inline "retry this stage" button on failed stages.
**Effort:** half a day.

### 13. Cost projection before running
**Status:** the UI shows post-run cost. Users only see what they spent after they've spent it.
**Path forward:** estimate input tokens from the loaded transcript and constraint text, multiply by the active stage models' input rate, render in the sidebar before the Synthesize button. Already implemented in the sidebar's pre-run cost panel — extend with output-token estimate.
**Effort:** half a day.

### 14. Compare-mode (run two providers in parallel)
**Status:** wired into the orchestrator (`mode="compare"`) but the UI surface is minimal.
**Path forward:** side-by-side diff in the Epics tab, per-stage cost split, and a "winner" banner based on the LLM-as-judge score.
**Effort:** 1-2 days.

### 15. Vision input fully wired
**Status:** `--vision-image` flag and a `VisionAttachment` type exist. Vision-capable models accept the attachment, but the UI flow for uploading photos in the sidebar is admin-only and lightly tested.
**Path forward:** promote vision input to general availability once OCR fallback is in place for non-vision-capable models.
**Effort:** 1 day.

---

## Accepted limitations (won't fix in v1)

- **Single-language output (English).** Prompts are English; output mirrors the input language but stories are always in English. Multilingual support is a v2 question.
- **Story Writer is not iterative.** Each story is generated in one shot. We don't ask the model to refine after seeing the gap detector's verdict. Acceptable for a v1 demo system; not for an autonomous workflow.
- **No human-in-the-loop checkpoints.** The pipeline is fire-and-forget. A reviewer can edit the output, but there's no in-flight approval gate. This is intentional for the v1 surface.

---

## Summary

| Severity | Items |
| --- | --- |
| P0 | 5 (auth, secrets, output authz, rate limits, default-on redaction) |
| P1 | 5 (audit durability, observability, cold start, provider failover, CI gates) |
| P2 | 5 (Chroma, failure UI, cost projection, compare-mode polish, vision GA) |

Total: 15 named gaps. Estimated total effort to reach a defensible production state: **12-15 person-days**. None of the items require architectural rework — they extend the existing scaffolding.
