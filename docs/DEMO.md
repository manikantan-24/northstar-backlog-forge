# Demo runbook — Backlog Synthesizer

A tight ~3-minute live demo: **messy inputs → audited multi-agent synthesis → real Jira tickets**.

## Before you start
```bash
make ui            # launches Streamlit at http://localhost:8501
```
- Confirm the **API key status** is green in the sidebar.
- For the Jira step, confirm `JIRA_*` are set in `.env` (the **Create in Jira** button only appears after a run when configured).
- Recommended once, beforehand: seed the live Jira project so duplicates show up —
  `python scripts/seed_jira.py` (and `python scripts/seed_confluence.py` for live wiki).

## The script

**1. Frame it (15s).** "Five specialized agents turn a meeting transcript, an architecture wiki, and our existing backlog into epics, stories with Given/When/Then acceptance criteria, and tasks — flagging duplicates, conflicts, and gaps, with a full audit trail." Click **❓ Help** (top-right) to show the pipeline overview.

**2. Pick multiple sources (20s).** In **Transcript**, select *two* samples (e.g. "Meeting notes — NorthStar Q3" + "Pharmacy refill escalation") to show multi-source combining. Leave **Architecture / wiki** on the recommended page and **Existing backlog** on the 30-ticket JIRA sample (so RAG + duplicate detection engage).

**3. Add vision (15s).** Expand **"Add whiteboard photos / screenshots"** → tick the **Whiteboard — sprint planning sketch** sample. Set the preset to **Premium** (or turn **Auto-switch** on in Options) so the vision-capable Parser reads the image.

**4. Synthesize (45s).** Click **▶ Synthesize**. Narrate the **accumulating live log** as each agent fires (`▸ started → ✓ completed`, with token counts and elapsed time). The five pipeline stages light up in sequence.

**5. Walk the output (40s).**
- **Epics** tab → expand an epic → a story with acceptance criteria, priority, and the **evidence quote** traced back to the transcript.
- **Conflicts / Gaps / Duplicates** tabs → the PCI-blocked offline-card story as a conflict; duplicates against the seeded backlog.
- **Audit trail** tab → expand one agent to show the exact prompt + response (the "how it reached this" story).
- **Cost & tokens** panel → per-agent spend.

**6. Close the loop (25s).** Click **⤴ Create in Jira** (top-right) → *Create* → the created **Epic → Story → Sub-task** appear as clickable links into the live project. "From a messy meeting to a real, audited sprint backlog in under a minute."

## Talking points if asked
- **Why five agents?** One job each, shared audited memory, partial-failure recovery. We measured it: vs. a single mega-prompt, quality is comparable on small inputs (0.88 vs 0.84 deterministic) — the win is auditability, cost control, duplicate-detection, and scale (Appendix C/D of the technical doc).
- **Resilience:** with **Auto-switch** on, a provider outage on one stage fails over to the other vendor — shown as an amber ⚠ FAILOVER line, never silent.
- **Honesty:** runs on **mock data for a fictional client (NorthStar Retail)**; live Atlassian is optional. Evaluation numbers and the single-prompt baseline are committed under `evaluation/results/`.

## Fallbacks during the demo
- A provider hiccups → turn **Auto-switch** on (Options) and re-run; you'll see the failover line instead of a dead run.
- No live Jira → the **Export** dialog (top-right) downloads `synthesis.md` / `.json` instead.
- Vision ignored → ensure the Parser is a Claude model (Premium preset, or Auto-switch on).
