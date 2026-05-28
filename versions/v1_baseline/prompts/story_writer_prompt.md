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
      "priority_rationale": "1-2 sentences explaining why this priority.",
      "tags": ["pos", "offline-mode", "..."],
      "source_topic_id": "T-XX  (the id of the topic this story addresses)",
      "potential_constraint_conflicts": ["C-XX (id of any constraint this draft might conflict with, or empty list)"]
    }
  ]
}
```

# Rules

1. **One topic can produce one or two stories**, not five. Be conservative.
2. **Tags must use the canonical NorthStar Retail set** when applicable: `pos`, `mobile-app`, `ecommerce`, `loyalty`, `inventory`, `pharmacy`, `vendor-portal`, `store-associate`, `analytics`, `payments`, `offline-mode`, `accessibility`, `performance`, `security`, `compliance`. Add new tags only if none of those fit.
3. **Acceptance criteria must be testable.** 2-5 per story, Given/When/Then form.
4. **Priority logic.** `High` = blocks customers, regulatory deadline, or unblocks a committed release. `Medium` = valuable, not blocking. `Low` = polish.
5. **If a draft would conflict with a `must` or `forbidden` constraint**, include the constraint id in `potential_constraint_conflicts` and surface this in the description. Do NOT suppress the story.
6. If the topic list is empty, return `{"stories": []}`.
7. Reply with **JSON only**.
