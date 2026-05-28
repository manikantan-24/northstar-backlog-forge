You will be given a list of draft user stories. Your task is to group them into **epics** (cohesive themes) and break each story into **3-7 concrete implementation tasks** that an engineering team could pick up directly.

# Stories (from the Story Writer Agent)

```json
{{STORIES_JSON}}
```

# What to produce

A JSON object of this exact shape:

```json
{
  "epics": [
    {
      "title": "Short title (e.g. 'POS Offline Resilience').",
      "description": "1-2 sentences describing the cohesive theme that links the stories under this epic.",
      "stories": [
        {
          "id": "ST-XX (carry over from input)",
          "title": "... (verbatim from input)",
          "description": "... (verbatim from input)",
          "user_story": "... (verbatim from input)",
          "acceptance_criteria": ["... (verbatim from input)"],
          "priority": "... (verbatim from input)",
          "priority_rationale": "... (REQUIRED: verbatim from input)",
          "tags": ["... (verbatim from input)"],
          "source_topic_id": "... (verbatim from input — required for audit traceability)",
          "evidence": [{ "... (verbatim from input — required for audit traceability)": "..." }],
          "potential_constraint_conflicts": ["... (verbatim from input — required so the Gap Detector sees them)"],
          "tasks": [
            {"title": "Concrete implementation task (one short sentence).", "type": "backend | frontend | data | infra | qa | spike"},
            ...
          ]
        }
      ]
    }
  ]
}
```

# Rules

1. **Every story must end up under exactly one epic.** No orphans.
2. **Epics are themes, not buckets.** Group by shared platform area, customer journey, or technical concern — not by priority or arbitrary category.
3. **3-7 tasks per story.** Each task should be a concrete unit of engineering work a team member could be assigned. "Add CSV export endpoint" is a task; "Improve performance" is not.
4. **Task types** use the listed enum: `backend`, `frontend`, `data`, `infra`, `qa`, `spike`. A `spike` is an investigation that has to land before implementation.
5. **Preserve EVERY story field verbatim** when carrying them through — not just the ones listed above. The schema lists the minimum required fields; if the input story has additional fields (e.g. `notes`, `tags_extra`, anything else), copy them through unchanged. Do NOT rewrite titles, acceptance criteria, rationale, evidence, or conflict references. Add `tasks` as the only new field per story.
6. **Audit-required fields MUST appear on every story:** `priority_rationale`, `source_topic_id`, `evidence`, `potential_constraint_conflicts`. These fields drive the audit trail and the Gap Detector's downstream logic — dropping them silently breaks traceability and the conflict detector. If the input has them, the output must have them.
7. Reply with **JSON only**.
