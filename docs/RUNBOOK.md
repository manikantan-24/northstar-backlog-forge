# Backlog Synthesizer — Operator Runbook

This is the playbook for the on-call / first-responder when something
goes wrong. It is deliberately *prescriptive*: read top-to-bottom, run
the commands, escalate if you reach the end without resolution.

Scope: the multi-agent CLI (`python src/main.py`), the Streamlit UI
(`streamlit run app.py`), and the eval suite
(`python evaluation/run_evaluation.py`).

---

## 1. Startup, shutdown, restart

### Local — CLI mode

```bash
# One-shot synthesis
python src/main.py \
    --transcript samples/meeting_notes.txt \
    --constraints samples/architecture_constraints.md \
    --backlog samples/jira_backlog.json
```

Outputs land under `outputs/<timestamp>/`. No long-running process to
manage in CLI mode — each invocation runs once and exits.

### Local — UI mode

```bash
streamlit run app.py
# defaults to http://localhost:8501
```

To stop: `Ctrl-C` in the terminal, or `pkill -f "streamlit run"`.

### Docker

```bash
docker build -t backlog-synthesizer:latest .
docker run -d --name bs \
  -p 8501:8501 \
  --env-file .env \
  -v "$PWD/outputs:/app/outputs" \
  backlog-synthesizer:latest

docker stop bs   # graceful — Streamlit handles SIGTERM
docker start bs  # restart in place
docker rm -f bs  # nuke
```

The `outputs/` volume mount preserves run artifacts across container
restarts. If you don't mount it, every container restart loses prior runs.

---

## 2. Health checks

### UI mode

```bash
curl -f http://localhost:8501/_stcore/health
# 200 OK = healthy
```

### CLI mode

There is no daemon to probe. A successful synthesis prints a summary
block ending with `Synthesis: outputs/<timestamp>/synthesis.md`.
Exit code 0 = success. Non-zero codes:
- `2` — input file couldn't be read (bad path, unsupported extension)
- `3` — orchestrator failed unexpectedly (check logs for traceback)

### Agent-level health

The audit log (`outputs/<timestamp>/audit_trail.md`) records each agent's
`started` and `completed` events. Missing `completed` = that agent
errored; check the orchestrator log line for which one.

---

## 3. Common failures and what to do

### "ANTHROPIC_API_KEY isn't set"

The agent's `ClaudeTool` couldn't find a key.

```bash
# Confirm
grep ANTHROPIC_API_KEY .env
# If missing, copy from the example
cp .env.example .env
# Then edit .env and paste the real key
```

Inside Docker: ensure you passed `--env-file .env` to `docker run`.

### "anthropic.RateLimitError"

You hit the Anthropic per-minute rate limit. The Claude tool retries
with exponential backoff (tenacity) up to 3 attempts. If it still
fails:
- Wait 60 seconds and re-run.
- If hitting it repeatedly, check the [Anthropic console](https://console.anthropic.com/)
  for your account's rate-limit tier and request an increase.

### "memory.store: sentence-transformers not installed — falling back to no-embedding mode"

The vector memory engine is in degraded mode. Synthesis still runs but
the Gap Detector skips semantic ticket retrieval (it falls back to
considering all tickets, which is fine for ≤30 tickets but doesn't
scale).

```bash
pip install sentence-transformers
# or for a full re-install
pip install -r requirements.txt
```

In Docker this should never happen — the image builds with
sentence-transformers pre-installed.

### "Pipeline failed: No JSON object found in model output"

The LLM returned prose instead of JSON. `ClaudeTool.call_for_json` does
defensive parsing (handles fenced / prose-wrapped output), so this is
rare. When it happens:
- Re-run — most failures are transient.
- If reproducible: inspect the bad response. Add a logging line
  (`logger.warning("Bad JSON response: %s", text)`) in
  `tools/claude_tool.py` and re-run to capture what came back.
- If the prompt was the cause, see Section 9 (Updating prompts safely).

### "AgentError: <Agent> LLM call failed"

The named agent's Claude call failed after retries. The orchestrator
records the failure and skips downstream agents that depend on it:
- Parser failure → Story Writer is skipped → Epic Decomposer skipped → Gap Detector skipped
- Constraint Extractor failure → does NOT block downstream (constraints are advisory)
- Story Writer failure → Epic Decomposer + Gap Detector skipped
- Epic Decomposer failure → Gap Detector still runs (operates on stories, not epics)
- Gap Detector failure → synthesis still ships, just with no gap/conflict/duplicate detection

Partial synthesis is still written to `outputs/<timestamp>/`.

---

## 4. Key rotation

### Anthropic key

1. Create the new key in the Anthropic console first.
2. Update `.env` with the new key.
3. If running in Docker: `docker stop bs && docker rm bs && docker run -d ...` (with `--env-file .env`).
4. Verify the next synthesis succeeds.
5. Only then, **revoke** the old key in the console.

This order — issue new, swap in, verify, revoke old — avoids a window
where neither key works.

### .env.example consistency

After any new env var is added, update `.env.example` so a fresh clone
knows what to fill in. The CI Docker build verifies the image starts
but doesn't verify your `.env` has every required var.

---

## 5. Audit log interpretation

Each run produces an `audit_trail.md` under `outputs/<timestamp>/`.
Read top-to-bottom; it's chronological. Every agent emits at minimum:
- A `started` event with input characteristics (char count, topic count, etc.)
- A `tool_call` event per Claude invocation (with prompt-char count, response excerpt, token usage)
- A `completed` event with what was written to memory

### When the synthesis looks wrong

- **Too many stories** → Story Writer's `completed` event will show the
  count. If it's clearly out of proportion to the topics (say, 10
  stories from 2 topics), the prompt drifted or the model
  over-generated. Check `prompts/story_writer_prompt.md` — was it
  edited recently?
- **A topic the user expected is missing** → Parser's `completed` event
  lists topic IDs. If it's not there, Parser missed it. Re-read the
  transcript — sometimes the topic was implicit and the parser
  reasonably skipped it.
- **A duplicate / conflict that should have fired didn't** → Gap
  Detector's audit entry shows which existing tickets were considered
  for each story (`indexed_tickets` event). If the expected match isn't
  in the candidate list, the semantic-retrieval cutoff filtered it
  out. Re-run with a smaller backlog (or temporarily lower `TOP_K` in
  `gap_detector_agent.py`).

### Run metadata

Every run captures a header in `audit_trail.md` with:
- Timestamp (UTC)
- Input character counts per source
- Total token usage across all agents
- Final synthesis counts (epics, stories, gaps, conflicts, duplicates)

---

## 6. Cost spike playbook

Cost per run on Claude Sonnet (current pricing): roughly $0.10–0.30 for
the bundled sample. The eval suite (4 cases) is $1–2 per full pass.

### When a run takes > $1

```bash
grep "tokens_used" outputs/<timestamp>/audit_trail.md
```

This lists per-agent token usage. The usual suspects:
- **Parser** with a giant transcript → trim the transcript before re-running.
- **Story Writer** when there are too many topics → tighten the
  Parser's prompt to consolidate topics; or run with `--dry-run` first
  to see topic count.
- **Gap Detector** with a huge backlog → it embeds every existing
  ticket. > 500 tickets makes this expensive. Pre-filter before
  passing to the synthesizer.

### When eval cost is unexpectedly high

The eval CI job runs on push-to-main and on workflow_dispatch. If it's
firing more than expected, check `.github/workflows/ci.yml` and verify
the gating logic. The `eval-suite` job is the only one that burns API
credit.

---

## 7. Adding a new LLM provider

This project is intentionally Claude-only — the multi-agent pattern
assumed a single high-quality reasoner. If you must add another:

1. Add a new `tools/<provider>_tool.py` implementing the `Tool` base
   class with at minimum `call_for_json(prompt, max_tokens) → (dict, usage)`.
2. Mirror `ClaudeTool`'s tenacity retry decorator.
3. Wire it into `Orchestrator.__init__` so agents can pick by name.
4. Update prompts — Claude responds well to direct JSON instructions;
   other models may need stricter formatting (json mode, explicit
   "return JSON" directives, etc.).
5. Add at least one unit test in `tests/` that mocks the new tool.
6. Add a costing entry — eval cost varies by provider.

---

## 8. Disaster recovery

### Lost outputs/ directory

Outputs are reproducible — re-run the synthesizer against the same
inputs. Output is not deterministic (LLMs aren't) but should be
materially similar. Each run is timestamped, so re-runs don't overwrite.

### Lost golden dataset

`evaluation/golden_dataset/` is checked into git. Restore from git.

### Lost .env

```bash
cp .env.example .env
# Re-paste your API key from a secrets manager (1Password, Bitwarden, etc.)
# NEVER commit .env — it's in .gitignore for a reason.
```

### Container image broken / missing

```bash
docker build -t backlog-synthesizer:latest .
```

The CI workflow runs this same build on every push to main, so a known-
good Dockerfile is always reachable via `git checkout main`.

---

## 9. Updating prompts safely

Prompts live in `prompts/*.md`. Each is loaded by exactly one agent at
init time. Updating a prompt is a real code change:

1. **Branch first.** Never edit prompts on main.
2. **Run the eval suite** before and after:
   ```bash
   python evaluation/run_evaluation.py > before.txt
   # edit the prompt
   python evaluation/run_evaluation.py > after.txt
   diff before.txt after.txt
   ```
3. If the eval suite regresses (any case that passed before now fails),
   either roll back the prompt or update the case if the new behavior
   is actually correct.
4. Commit the prompt change AND any eval-case adjustment together.

---

## 10. Quick reference — file locations

| What | Where |
|---|---|
| CLI entry | `src/main.py` |
| Streamlit UI entry | `app.py` |
| Orchestrator | `src/orchestrator.py` |
| Agents | `src/agents/*.py` |
| Tools | `src/tools/*.py` |
| Memory + audit log | `src/memory/*.py` |
| PII redactor | `src/redactor.py` |
| Prompts (one per agent) | `prompts/*.md` |
| Sample inputs | `samples/*.{txt,md,json}` |
| Golden dataset | `evaluation/golden_dataset/` |
| Metrics + LLM-as-judge | `evaluation/{metrics,llm_as_judge}.py` |
| Eval runner | `evaluation/run_evaluation.py` |
| Run outputs | `outputs/<timestamp>/` |
| Unit tests | `tests/` |
| CI workflow | `.github/workflows/ci.yml` |
| Dockerfile | `Dockerfile` |
| Architecture diagram | `architecture.md` |

---

## 11. When you don't know what to do

1. Read the audit log for the failing run — it's chronological and tells
   you which agent broke.
2. Re-run with `--dry-run` to confirm inputs load correctly without
   spending API credit.
3. Run the unit suite (`pytest tests/`) — if it fails, the regression
   is in the code, not the inputs.
4. Check `git log -5 --oneline` for recent changes.
5. If still stuck, capture: the failing `audit_trail.md`, the input
   files, and the exact error / traceback. File an issue with all three.
   Do NOT paste raw `.env` content into an issue.

---

## Future Enhancements

The items below are known gaps agreed during the v3 demo build. Prioritised by business impact. None block the demo; all block a production multi-tenant rollout.

---

### P1 — Required before any production customer

**1. Approval chain (contributor submits → lead approves → Jira push)**
Today any contributor can push AI-generated stories directly to Jira after ticking a checkbox. Enterprise governance requires a two-step gate: contributor submits for review → assigned lead/PM approves → push fires.
_Implementation path:_ Add a `pending_approval` run state, email/Slack notification to approver, approval action (accept/reject with comment), audit logged.

**2. Iterative refinement loop**
The pipeline is one-shot. Gap Detector finds conflicts but there is no "re-draft affected stories" loop.
_Implementation path:_ After Gap Detector completes, surface conflicts per-story; add "Re-draft this story" button that calls Story Writer agent in isolation with constraint feedback injected into the prompt.

**3. Two-way Jira sync / duplicate-push guard**
Re-running synthesis on an already-pushed backlog creates near-duplicate stories in Jira.
_Implementation path:_ Before Jira push, run a JQL query to check if epics/stories with the `backlog-synth` label already exist; surface a "already pushed — update or skip?" choice.

**4. Project / workspace isolation**
All runs share a single Jira project and Confluence space. Large organisations have 10–20 product squads running synthesis concurrently, each with their own backlog.
_Implementation path:_ Add a `workspace` concept: workspace = `{jira_project, confluence_space, team_members, feature_flags, run_history}`. Per-workspace admin role.

---

### P2 — Close within the first month of production

**5. Story quality scores surfaced per-story in UI**
The LLM-as-judge can score acceptance criteria quality but the score is only in the evaluation harness, not the live UI.
_Implementation path:_ Run a lightweight AC quality check (completeness heuristic, no LLM call) per story post-synthesis and render a quality badge next to each story card in the Epics tab.

**6. Conflict resolution workflow**
Conflicts surface (story X violates constraint C-04) but there is no resolution path — no assign, no resolve, no audit note.
_Implementation path:_ Add a "Resolve" action per conflict: opens a dialog for resolution note + assignee + mark-resolved. Audit logged with resolver identity and justification.

**7. Triggered / scheduled ingestion**
All runs are manual. Enterprise teams want auto-trigger: meeting ends → Teams/Zoom transcript webhook → synthesis → notification → ready for review.
_Implementation path:_ Add an inbound HTTP endpoint (FastAPI sidecar or Streamlit `/webhook` route) that accepts a transcript payload and queues a synthesis run. The MCP server's `synthesize_backlog` tool is halfway there.

**8. Audit trail export to compliance systems**
The SHA-256 hash chain is solid for tamper-evidence but lives in a local SQLite file. Compliance teams need to push audit events to a SIEM (Splunk, Azure Sentinel) or attach to a ServiceNow change record.
_Implementation path:_ Add an `/audit/export?run_id=<id>` endpoint returning the full event chain as JSON/CSV. Add an optional SIEM webhook (`SIEM_WEBHOOK_URL`) that fires per-run alongside the existing Slack notification.

---

### P3 — Quality of life

**9. Run-to-run diff (compare current vs. last synthesis on same backlog)**
A/B mode compares two model presets but cannot compare "this sprint vs. last sprint" for the same backlog.
_Implementation path:_ Add a "Compare with previous run" picker in run history that loads two `synthesis.json` files and renders a structural diff (added/removed stories, gap delta, conflict delta).

**10. ChromaDB in server mode for multi-replica**
ChromaDB embedded mode is fine for single-replica. Multi-replica Azure Container Apps each get their own in-process ChromaDB with no shared state.
_Implementation path:_ Add a `chromadb-server` container to the Terraform config; switch `USE_CHROMADB=1` to point at the server URL instead of the local embed.

**11. Append-only cloud audit store**
SQLite is a single-host file. Multi-replica deployments produce disconnected audit chains.
_Implementation path:_ Write audit events to Azure Blob with immutable storage policy (or Sigstore Rekor). Keep SQLite as local cache; cloud store as compliance record.

---

_Last updated: 2026-06-13 during v3 demo hardening pass._
