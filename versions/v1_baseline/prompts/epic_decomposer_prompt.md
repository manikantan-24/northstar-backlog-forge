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
          "title": "...",
          "description": "...",
          "user_story": "...",
          "acceptance_criteria": [...],
          "priority": "...",
          "tags": [...],
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
5. **Preserve story fields verbatim** when carrying them through — don't rewrite titles or AC. Add `tasks` as the only new field per story.
6. Reply with **JSON only**.
