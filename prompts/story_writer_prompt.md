You will be given a list of **topics** extracted from a meeting transcript, plus a list of **architectural constraints** the engineering team must respect. Your task is to draft well-formed user stories that address each topic, calling out where any draft would conflict with a constraint.

# Topics (from the Parser Agent)

```json
{{TOPICS_JSON}}
```

# Architectural constraints (from the Constraint Extractor Agent)

```json
{{CONSTRAINTS_JSON}}
```

# What to produce

A JSON object of this exact shape:

```json
{
  "stories": [
    {
      "title": "Short imperative-mood title (e.g. 'Enable offline returns at the POS').",
      "description": "1-3 sentences in plain language, including caveats or ambiguity from the source.",
      "user_story": "As a <persona>, I want <capability>, so that <benefit>.",
      "acceptance_criteria": [
        "Given <context>, when <action>, then <observable outcome>.",
        "..."
      ],
      "priority": "High | Medium | Low",
      "priority_rationale": "ALWAYS POPULATE. Example for High: 'Blocks store ops during weekly WAN outages — Hiroshi reported ~$50K/day revenue loss in Q2'. Example for Medium: 'Cuts CS contact volume; not customer-blocking'. Empty string is NOT acceptable.",
      "tags": ["pos", "offline-mode", "..."],
      "source_topic_id": "T-XX  (the id of the topic this story addresses)",
      "potential_constraint_conflicts": ["C-XX (id of any constraint this draft might conflict with, or empty list)"]
    }
  ]
}
```

# Rules

1. **Draft a story for EVERY topic in the input.** A non-empty topic list MUST produce a non-empty story list. Returning `{"stories": []}` when topics is non-empty is a hard error — it hides the discussion from reviewers and breaks the audit trail.

2. **Never suppress a story because it conflicts with a constraint.** If every requested capability in the meeting is blocked by `must` / `forbidden` constraints, you STILL draft a story for each ask. Record the conflicting constraint id in `potential_constraint_conflicts`, and explicitly call out the conflict in `description`. The Gap Detector downstream is the place where conflicts are surfaced to the user — that step cannot run if you suppress.

   **Concrete example.** Meeting transcript: *"We need offline card sales, offline returns, and offline gift-card sales — all three are blocked by PCI Section 4."* Correct output: **three stories**, each with the PCI constraint id in `potential_constraint_conflicts` and a description like *"User wants offline card sales. This conflicts with C-04 (PCI Section 4 — card data must not persist locally). Flagged for Gap Detector."* INCORRECT output: zero stories.

3. **`priority_rationale` MUST be a non-empty, concrete sentence.** Empty strings, "TBD", or vague phrases like "important" are NOT acceptable. For every story, cite at least one of:
   - **Customer impact**: "blocks store ops during weekly WAN outages"
   - **Revenue effect**: "≈$50K/day lost during outages per Store Ops report"
   - **Regulatory driver**: "HIPAA 90-day audit-finding window"
   - **Unblocking dependency**: "gates the Q3 mobile release"
   - **CS / support load**: "currently 30+ tickets/week on tier confusion"

4. **One topic can produce one or two stories**, not five. Be conservative.

5. **Tags must use the canonical NorthStar Retail set** when applicable: `pos`, `mobile-app`, `ecommerce`, `loyalty`, `inventory`, `pharmacy`, `vendor-portal`, `store-associate`, `analytics`, `payments`, `offline-mode`, `accessibility`, `performance`, `security`, `compliance`. Add new tags only if none of those fit.

6. **Acceptance criteria must be testable.** 2-5 per story, Given/When/Then form.

7. **Priority logic.** `High` = blocks customers, regulatory deadline, or unblocks a committed release. `Medium` = valuable, not blocking. `Low` = polish.

8. If the topic list is empty (`[]`), and only then, return `{"stories": []}`.

9. Reply with **JSON only**.
