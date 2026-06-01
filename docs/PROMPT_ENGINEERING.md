# Prompt engineering

Six prompts in this project: one shared system prompt + five agent-specific prompts. This document explains the design principles, what's in each prompt, and what we iterated on.

## Four design principles

1. **One prompt, one reasoning task.** Each agent prompt has a single clear deliverable. Cramming multiple jobs into one prompt makes every job worse.
2. **Be specific about output shape.** Every prompt declares the exact JSON schema with example field values. Don't hope the model figures it out.
3. **Give the model rules, not just instructions.** "Be conservative" is a rule that generalizes. "Return at least 5 stories" is an instruction that breaks on edge cases.
4. **Tell the model how to handle ambiguity.** Otherwise it invents.

## The six prompts

### System prompt (`prompts/system_prompt.md`)

Shared across every agent. Casts the model as "an experienced agile delivery lead embedded in NorthStar Retail's engineering organization" with concrete domain context (2,000 stores, the named platforms, the customer segments).

Two non-negotiable rules:
> Only invent stories, constraints, gaps, or conflicts that are clearly grounded in the source material. Do not pad output.
> Prefer fewer high-quality items over many vague ones.

These dramatically reduce hallucination. The model is also told it's "not a product owner — you are a translator," which sets the right humility level for the task.

### Parser prompt (`prompts/parser_prompt.md`)

Job: turn raw transcript into a list of *topics* (not stories yet).

Key rules:
- Group small related asks under one topic
- Skip topics where the team explicitly said no
- Skip pure logistics (coordination, not engineering)
- Return empty list if nothing actionable

Why topics-not-stories at this stage: separating "what was said" from "what we'd build" makes the parser robust to noisy input. Stories require committing to a persona and a benefit — the parser doesn't need to do that. The Story Writer does it later.

### Constraint Extractor prompt (`prompts/constraint_extractor_prompt.md`)

Job: pull architectural constraints (must/should/forbidden) from a wiki page.

Key technique:
- Severity enum (`must`, `should`, `forbidden`) so downstream agents can decide what counts as a "real" conflict
- Category enum (`integration`, `performance`, `security`, `compliance`, `platform`, `data`, `offline`)
- `applies_to` tags so the Story Writer can match constraint relevance per story
- Require a `source_excerpt` quote — keeps the constraint traceable

### Story Writer prompt (`prompts/story_writer_prompt.md`)

Job: draft stories from topics, conscious of constraints.

Key techniques:
- Inline JSON schema with example field values
- Canonical tag set (`pos`, `mobile-app`, `loyalty`, `inventory`, etc.) so tags are comparable across runs
- Priority rubric — `High` is defined ("blocks customers, regulatory deadline, or unblocks a committed release") not left to vibes
- Explicit `potential_constraint_conflicts` field — the model surfaces draft conflicts without suppressing the story
- `source_topic_id` cross-reference — every story traces back to a topic

### Epic Decomposer prompt (`prompts/epic_decomposer_prompt.md`)

Job: group stories into epics, then break each story into 3-7 tasks.

Key rules:
- Every story under exactly one epic (no orphans, no duplicates)
- Epics are themes, not buckets (group by platform area / customer journey / technical concern)
- Task type enum (`backend`, `frontend`, `data`, `infra`, `qa`, `spike`)
- Preserve story fields verbatim (don't rewrite titles or AC)

### Gap Detector prompt (`prompts/gap_detector_prompt.md`)

Job: find conflicts and gaps. Duplicates are detected separately by local embedding similarity, so the LLM prompt is scoped to conflicts + gaps only (it explicitly does not ask for duplicates).

Why one call for three jobs: these are all *comparison* tasks against the same set of inputs (stories + existing tickets + constraints). Splitting them would mean re-loading context three times. Same reasoning task, three slices of output.

Key techniques:
- **Positive/negative worked example** for duplicates: "Add CSV export" vs "Add PDF export" — same topic, *not* duplicates. This pinned down the model's notion of overlap.
- Confidence rubric (`high` / `medium` / `low`) — gives reviewers something to triage with
- Conflicts only against `must` / `forbidden` constraints (not `should`)
- Gaps must cite source evidence — prevents invention

## What we iterated on

These notes capture changes made during prompt tuning, similar to the v1 retrospective.

### Iteration 1 — Initial schemas were too loose

First draft of the Story Writer prompt asked for "acceptance criteria as a list of strings" with no format guidance. Output AC quality was inconsistent — sometimes Given/When/Then, sometimes prose. Fixed by adding the explicit format string `"Given <context>, when <action>, then <observable outcome>."` to the schema example. Quality jumped immediately.

### Iteration 2 — Tags were inconsistent

Without a canonical tag set, two runs would tag the same story `mobile`, `mobile-app`, `app`, or `consumer-app`. Hard to compare results across runs. Fixed by listing the canonical set in the prompt and asking the model to use exactly those.

### Iteration 3 — Conflicts vs. constraint_conflicts on the story

We initially had the Story Writer flag conflicts on each story AND the Gap Detector flag conflicts globally. Duplication. Fixed by having the Story Writer flag *potential* conflicts during drafting (cheap signal), and the Gap Detector make the *final* call on what counts as a real conflict requiring action.

### Iteration 4 — Epic decomposition was creating one epic per story

First version of the Epic Decomposer happily produced 7 epics for 7 stories — totally missing the point of grouping. Fixed by adding "Epics are themes, not buckets" and requiring 2-4 stories per epic for typical inputs.

### Iteration 5 — Gap Detector was too eager on gaps

First version flagged every absent capability as a gap, including things outside the system's scope (e.g., "the source mentions store managers want better dashboards but no story addresses this" — but this is owned by a different team). Fixed by adding "Be conservative. Gaps should be the ones a reviewer would genuinely raise in grooming" and requiring source evidence for every gap.

### Iteration 6 — Parser dropped "blocked" requests (the case_07 fix)

The conflict-heavy golden case (`case_07`) scored 0.33 deterministic / 0.00 judge: it produced **zero stories** even though the transcript contained three explicit feature requests (offline card sales, offline returns, offline gift-card sales), all blocked by PCI rules. The Story Writer was suspected first — but its Rule 2 already handles blocked asks (it even uses this exact transcript as its worked example). The real cause was one stage upstream.

The Parser's Rule 3 said *"skip topics where the team explicitly said no."* The model correctly read "PCI forbids that" as the team saying no, so it returned **zero topics** — and the Story Writer can't draft stories for topics it never receives. Two prompts in direct contradiction; the Parser runs first, so the Parser won.

The fix splits "declined" from "blocked": an idea the team *chose not to pursue* (descoped, parked) is skipped, but a capability someone *requested* that's blocked by a rule/policy **is kept as a topic** — because surfacing the conflict to reviewers is the entire point of the downstream Gap Detector. After the fix, a targeted re-run of `case_07` scored **1.00 deterministic / 0.70 judge** (Parser now emits 3 topics, Story Writer drafts 3 stories, Gap Detector flags the PCI conflict citing C-15 / C-07). This is the clearest example in the project of why splitting reasoning into separate agents needs the *contracts between* agents to agree — a rule added to fix one stage silently broke an upstream assumption.

## Why not Anthropic's tool-use feature instead of JSON

Anthropic offers a tool-use API that lets you declare a schema and the model emits structured output matching it — eliminating the JSON-fence parsing risk.

We didn't use it because:
- The schema would need to be duplicated in tool-use definitions + the prompt
- Our `_extract_json_block` is small (~20 lines) and well-tested
- Tool-use changes the API surface across all six prompts at once

Worth revisiting once the system is otherwise stable. The win would be guaranteed JSON, eliminating one class of failure.

## Where this could go further

- **Few-shot examples** in each agent prompt — currently the schema is described but no full worked input/output pair is given. Adding one realistic example per agent could push consistency higher.
- **Calibration prompts** — periodically ask the model "given these 5 outputs, rate which is highest-quality" to test that our prompts match what humans actually rate well.
- **Per-agent model selection** — Parser doesn't need Sonnet's reasoning; Haiku would do. Cost savings without quality loss. Easy to wire because each agent owns its `ClaudeTool` instance.
