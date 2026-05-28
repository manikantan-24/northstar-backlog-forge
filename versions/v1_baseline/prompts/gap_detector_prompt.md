You will be given:
1. A list of newly drafted user stories
2. For each new story, a shortlist of the most semantically similar existing JIRA/GitHub tickets (retrieved by embedding similarity)
3. The architectural constraints the engineering team must respect

Your task is to identify three distinct things:
- **Duplicates** — new stories that overlap significantly with an existing ticket (the same underlying work, not just the same topic)
- **Conflicts** — new stories that contradict a `must` or `forbidden` constraint
- **Gaps** — important capabilities that the source material implies but neither the new stories nor the existing backlog seem to cover

# New stories

```json
{{NEW_STORIES_JSON}}
```

# Candidate existing tickets per new story

```json
{{CANDIDATES_JSON}}
```

# Architectural constraints

```json
{{CONSTRAINTS_JSON}}
```

# What to produce

A JSON object of this exact shape:

```json
{
  "duplicates": [
    {
      "story_id": "ST-XX",
      "existing_id": "NS-123",
      "confidence": "high | medium | low",
      "reason": "One sentence explaining the overlap."
    }
  ],
  "conflicts": [
    {
      "story_id": "ST-XX",
      "with": "C-XX (constraint id)",
      "severity": "high | medium | low",
      "reason": "One sentence explaining the contradiction."
    }
  ],
  "gaps": [
    {
      "title": "Short label for the gap.",
      "description": "1-2 sentences describing what's missing and why it matters.",
      "evidence": "What in the source material suggests this gap exists."
    }
  ]
}
```

# Rules for duplicates

1. Only flag a pair if you'd genuinely expect a reviewer to merge them. Topic overlap is not enough — the underlying work must be the same.
2. Example: "Add CSV export" vs "Allow users to download report data as CSV" — **duplicate**. "Add CSV export" vs "Add PDF export" — **not a duplicate** (same topic, different work).
3. `high` = clearly the same work. `medium` = probably overlapping. `low` = related but might be distinct.
4. Empty list is fine and expected if nothing overlaps.

# Rules for conflicts

1. Only flag a conflict against `must` or `forbidden` constraints. Stories that conflict with `should` constraints are noted in the story's own `potential_constraint_conflicts` field, not here.
2. `severity` reflects how badly the conflict undermines the constraint, not the constraint's severity itself.

# Rules for gaps

1. A gap is an important capability that's *implied* by the conversation but neither the new stories *nor* the existing backlog seem to address.
2. Be conservative. Gaps should be the ones a reviewer would genuinely raise in grooming.
3. Don't invent gaps from nothing — every gap needs a sentence of evidence from the source.

Reply with **JSON only**.
