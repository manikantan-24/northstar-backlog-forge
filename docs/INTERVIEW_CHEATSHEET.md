# Interview cheat-sheet — Backlog Synthesizer

The ~12 most likely questions, each answerable in 2–3 sentences. Deeper detail in `TECHNICAL_DOCUMENT.md` (appendix pointers below).

**1. What is it? (30s)**
A five-agent AI pipeline that turns a meeting transcript, an architecture wiki, and an existing backlog into structured, audited engineering work — epics → user stories (Given/When/Then AC, priority) → tasks — and flags duplicates, conflicts, and gaps. Every LLM decision is captured in an audit trail, and the result can be pushed straight into Jira. *(§1)*

**2. Why bounded multi-agent, not one big prompt?**
One reasoning task per agent, a shared audited memory, and deterministic ordering give reproducibility, testability, partial-failure recovery, and a linear audit trail. We *measured* the alternative: a single mega-prompt scores comparably on small inputs but has no intermediate artifacts to audit and collapses on duplicate-detection at scale. *(§2.2, App. D)*

**3. Why five agents — not 1, 2, or 3?**
Five is the smallest number that gives each agent exactly one job; below that, an agent juggles two reasoning tasks and quality drops. We didn't split further because duplicates are better done by embeddings (no LLM) and extra splits just add cost without separation. *(§2.2)*

**4. Why not autonomous agents?**
Autonomous loops take an unbounded number of LLM calls and produce a call-graph that's hard to budget, test, and audit. A fixed pipeline always makes ~5 calls, runs the same way every time, and yields a linear trace — we traded adaptability for reproducibility on purpose. *(App. K)*

**5. Why Claude Sonnet + Gemini Flash? Why not GPT-4o / Opus / Haiku / local?**
The provider is chosen per stage: Claude Sonnet for the hardest reasoning (strong instruction-following + reliable JSON + vision), Gemini Flash for the mechanical stages (cheap/fast, free tier). Opus is overkill for structured extraction; Haiku's quality drop on the priority/dedup nuance wasn't worth it; GPT-4o would work but we standardized on one SDK; local models for the *embeddings* (free, offline) where similarity, not reasoning, is needed. *(App. L)*

**6. How does the RAG work?**
The Gap Detector is retrieve-then-rerank: a local sentence-transformer (`all-MiniLM-L6-v2`) embeds the backlog, cosine similarity retrieves the top-5 similar tickets per new story (skipped under 20 tickets), and the LLM reasons only over those candidates. Duplicates are decided by the embeddings directly (threshold 0.6, no LLM call). *(§6)*

**7. How did you validate the AI-generated code?**
A 7-layer stack: static checks, **128 mocked tests**, real end-to-end runs, a golden evaluation suite (+ LLM-judge + a single-prompt baseline), runtime guardrails, human review, and a full provenance/audit trail. Crucially we "verify against reality, not assertions" — e.g., the eval caught case_07 silently producing zero stories when all unit tests passed. *(App. O)*

**8. Explain the evaluation — what do 0.88 / 0.72 mean?**
Deterministic metrics (objective: counts, expected duplicates/conflicts) average **0.88**; the LLM-as-judge (qualitative: AC quality, priority justification, etc.) averages **0.72**. The judge runs lower mainly because it penalizes correct-empty cases (e.g. a cancelled meeting → zero stories scores 0.20 even though that's right) — we report both and explain where they diverge. *(App. C)*

**9. How does the Jira write-back close the loop?**
`publish_synthesis()` creates the result in live Jira as Epic → Story → Sub-task, with acceptance criteria/priority/conflict flags in each description; it's defensive about project configs (progressive fallback) and records partial failures. Triggered by `--publish-jira` (CLI) or the "Create in Jira" button (UI). *(§11)*

**10. What if a provider has an outage mid-run?**
With the **Auto-switch** toggle on, a stage that fails after retries is retried on the other provider (Claude↔Gemini), shown as an amber ⚠ FAILOVER line in the live log and a `provider_failover` audit event. Off by default so the exact preset is honoured and easy to verify — nothing changes silently. *(§5)*

**11. How do you guard against hallucination?**
Two layers: six deterministic post-synthesis guardrails (the `error`-level ones flag any story that doesn't trace to a real topic), and a provenance chain where every story carries the verbatim transcript quote it came from — evidence is *system-attached*, not model-produced, so it can't be fabricated. Ultimately the output is a reviewable draft for a human. *(§4, App. F)*

**12. How would you add a new model, or harden this for production?**
New model = one new `*Tool` mirroring `call_for_json` + a line in `_build_tool_for_model` + pricing/preset entries; the agents never change (App. M). For production: auth + per-user isolation, rate/cost limits, forced PII redaction, and two-way Jira sync — all scoped in `PRODUCTION_READINESS.md`.

---
**One-liner if you only remember one thing:** *"The LLM does judgment; deterministic Python does everything else — which is why it's auditable, testable with 128 mocked tests, cost-bounded, and now closes the loop into Jira."*
