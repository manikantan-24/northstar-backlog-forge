You will be given the body of an internal wiki or Confluence page from NorthStar Retail's engineering team. Your task is to extract the **architectural constraints** that downstream story-writing must respect: required integrations, platform limits, performance budgets, security/compliance rules, and explicitly forbidden approaches.

# Input

<wiki>
{{WIKI_CONTENT}}
</wiki>

# What to produce

Reply with a single JSON object of this exact shape:

```json
{
  "constraints": [
    {
      "severity": "must | should | forbidden",
      "category": "integration | performance | security | compliance | platform | data | offline | other",
      "statement": "A single declarative sentence describing the constraint in concrete terms.",
      "source_excerpt": "A direct quote from the wiki anchoring this constraint.",
      "applies_to": ["pos", "mobile-app", "loyalty", "inventory", "pharmacy", "ecommerce", "vendor-portal", "*"]
    }
  ]
}
```

# Severity definitions

- `must` — non-negotiable. Stories that contradict this should be flagged as conflicts.
- `should` — strongly preferred default. Deviating requires explicit justification.
- `forbidden` — explicitly banned. Stories that propose this are conflicts.

# Rules

1. **Stay literal.** Only extract constraints the wiki actually states. Don't infer "common sense" platform rules that weren't written down.
2. **Be specific.** "Must perform well" is not a constraint; "Mobile cart load p95 must stay under 1.5s on 3G" is.
3. **`applies_to`** uses the listed tags (or `*` for system-wide). Multi-tag is fine for cross-cutting rules.
4. **Skip narrative.** Background context like "our customers value reliability" is not a constraint.
5. Reply with **JSON only**.
