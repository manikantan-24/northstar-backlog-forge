# AI usage across the SDLC

The brief asks: *"AI usage must be documented throughout SDLC stages."* This document captures how AI tooling was used at each stage of building this system. It complements `REFLECTION.md` (which is more retrospective).

## 1. Problem framing

**AI used:** Yes — Claude as a thought-partner.

Before writing any code, we described the brief in plain English and asked: *"What's the smallest multi-agent system that satisfies this brief? Where will the design tension show up?"*

The model surfaced two things we hadn't fully anticipated:

- **The audit log is not optional.** The brief says "audit logs must show how conclusions were reached." A single-prompt design can't satisfy this — you only get a final answer, not a reasoning trace. This pushed us toward a pipeline of small agents whose individual decisions can each be logged.
- **The conflict-detection scope is broader than dedup.** "Conflicts" in this brief includes contradictions with architectural constraints, not just overlap with existing work. This is what motivated the separate Constraint Extractor agent — without it, the Story Writer has no input to check against.

These two framings shaped the architecture before we wrote a single line of Python.

## 2. Solution design

**AI used:** Yes — Claude as a design critic.

We drafted an initial pipeline (Parser → Story Writer → Gap Detector) and pasted it back to Claude with the question: *"What's missing? What will break first?"*

Three useful responses:

- **"Where does the architecture wiki content go?"** — confirmed we needed a dedicated agent for constraint extraction, not just stuffing wiki content into the Story Writer's prompt.
- **"Stories alone don't satisfy 'epics, stories, and tasks' in the brief."** — pushed us to add the Epic Decomposer as a dedicated step rather than retrofit hierarchy into the Story Writer.
- **"How do you bound the run? What if the model fails?"** — surfaced the need for explicit retry logic at the tool layer and per-agent graceful-degradation behavior.

The final five-agent design with explicit memory contracts came out of this back-and-forth. The architecture diagram in `architecture.md` reflects the result.

## 3. Prompt design

**AI used:** Yes — Claude as a prompt critic.

After drafting each agent prompt, we pasted it back with the question: *"What's wrong with this prompt? What edge cases will it fail on?"*

Useful responses across the six prompts:

- **Parser prompt:** the model pointed out that without an "empty list" escape valve, the parser would fabricate topics on noisy or off-topic input. Added the explicit rule.
- **Story Writer prompt:** suggested the canonical tag set to keep outputs comparable across runs.
- **Gap Detector prompt:** suggested the worked example pair ("CSV vs PDF — same topic, not duplicates") which pinned down the model's notion of overlap.

We didn't take every suggestion. The model recommended we ask for "draft user stories sorted by ROI" — overspecified and would have made the output less useful. The Story Writer just produces stories; ROI sorting is downstream.

## 4. Evaluation plan

**AI used:** Yes — Claude as a test brainstormer.

For the evaluation harness in `evaluation/`, we asked Claude: *"Given the synthesis output shape, what would a meaningful test of correctness look like? What are the failure modes humans would catch but a unit test would miss?"*

This produced the breakdown of:
- **Deterministic metrics** (story count in range, AC well-formed, required topics present, forbidden topics absent, expected duplicates found, expected constraint conflicts found) — implemented in `evaluation/metrics.py`
- **LLM-as-judge dimensions** (AC quality, priority justification, story granularity, tag accuracy, conflict reasoning) — skeleton in `evaluation/llm_as_judge.py`

The deterministic metrics catch the gross failures; the LLM-as-judge catches the subtle quality issues. Both are needed.

## 5. Sample data generation

**AI used:** Yes — Claude as a sample writer.

The NorthStar Retail sample files (`meeting_notes.txt`, `architecture_constraints.md`, `product_strategy.md`, `jira_backlog.json`, `github_issues.json`) were drafted with AI help. The request:

> "Realistic enterprise content for a fictional retail giant called NorthStar Retail (~2,000 stores nationwide; full-line: grocery, electronics, apparel, pharmacy, auto). Generate a Q3 planning meeting transcript that is deliberately conversational and messy. Include cross-references to specific JIRA tickets we'll seed in the backlog. Include an explicit 'we said no' section. Include at least one customer-facing problem that would conflict with the architecture constraints I'll provide separately."

The first draft was too clean and too short. Revision: "more conversational, with side-comments, unfinished sentences, and the kind of dropped 'we should talk about this later' threads you'd see in a real planning meeting." Final samples are the third revision.

For the JIRA backlog (30 tickets), we asked for "a mix of statuses (in-progress / ready / backlog / blocked), priorities, and themes that includes 3-4 items that overlap thematically with the asks in the meeting notes." The cross-references in the resulting JSON are deliberate so duplicate detection has real overlap to find.

## 6. Implementation

**AI used:** Yes — Claude as a coding pair.

While building out the multi-agent framework, we used Claude to:

- Sanity-check the `MemoryStore` interface before implementing it
- Review the orchestrator's failure-handling logic
- Suggest test cases for the JSON-fence parser (which caught the prose-wrapped-JSON case)
- Critique the audit log schema — the model suggested separating `payload` (structured) from `reasoning` (human-readable) which made the rendered audit trail much more legible

We did *not* use Claude to write entire files end-to-end. Each module was hand-architected, with Claude consulted on specific design decisions or edge cases.

## 7. Documentation

**AI used:** Yes — for drafting + review.

This document, the architecture overview, the prompt-engineering notes, and the agent-design rationale were drafted with AI assistance. We asked for a "comprehensive but readable" tone and reviewed every paragraph for accuracy against the actual code.

The samples/README.md (which explains the NorthStar Retail fiction) was generated after the sample data was finalized, so the cross-references it cites are guaranteed to match what's in the files.

## Summary of where AI was *not* used

- The directory structure and file layout are hand-designed.
- The CLI argument shape is hand-designed.
- The Streamlit UI design (when added) is hand-designed.
- The choice of dependencies (`tenacity`, `chromadb`, `python-docx`, etc.) is hand-chosen.
- The decision to use a bounded pipeline over a free-form agent loop was a deliberate human call, not a model recommendation.

## What this means for an interview

If asked *"Show me where AI is used in this project"*, the answer has two halves:

1. **At runtime** — Claude is called once per agent (5-7 calls per run) inside the orchestrator pipeline. Bounded, audited, retry-safe.
2. **During development** — Claude was a thought-partner at every SDLC stage: framing the problem, designing the architecture, critiquing prompts, brainstorming tests, generating realistic samples, and reviewing documentation.

The runtime usage is what the system *does*. The development usage is how it was built. Both matter for the brief.
