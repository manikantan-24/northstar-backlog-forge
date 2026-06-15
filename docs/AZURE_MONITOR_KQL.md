# Azure Monitor — KQL Queries for Grafana Dashboard

These queries run against the `ContainerAppConsoleLogs_CL` table in the
`law-backlog-synthesizer` Log Analytics Workspace.

Every query parses the `Log_s` column for lines where `event == "pipeline_completed"`,
which the app emits as a single JSON object on stdout after every synthesis run.

---

## Panel 1 — Total Runs (stat panel)

```kql
ContainerAppConsoleLogs_CL
| where ContainerName_s == "backlog-synthesizer"
| where Log_s has "pipeline_completed"
| extend d = parse_json(Log_s)
| where tostring(d.event) == "pipeline_completed"
| summarize TotalRuns = count()
```

---

## Panel 2 — Runs Over Time (time series)

```kql
ContainerAppConsoleLogs_CL
| where ContainerName_s == "backlog-synthesizer"
| where Log_s has "pipeline_completed"
| extend d = parse_json(Log_s)
| where tostring(d.event) == "pipeline_completed"
| summarize Runs = count() by bin(TimeGenerated, 1h)
| order by TimeGenerated asc
```

---

## Panel 3 — Average Run Duration in seconds (stat panel)

```kql
ContainerAppConsoleLogs_CL
| where ContainerName_s == "backlog-synthesizer"
| where Log_s has "pipeline_completed"
| extend d = parse_json(Log_s)
| where tostring(d.event) == "pipeline_completed"
| extend elapsed = todouble(d.elapsed_seconds)
| summarize AvgDuration = avg(elapsed), P95Duration = percentile(elapsed, 95)
```

---

## Panel 4 — Run Duration Over Time (time series)

```kql
ContainerAppConsoleLogs_CL
| where ContainerName_s == "backlog-synthesizer"
| where Log_s has "pipeline_completed"
| extend d = parse_json(Log_s)
| where tostring(d.event) == "pipeline_completed"
| extend elapsed = todouble(d.elapsed_seconds)
| summarize AvgDuration = avg(elapsed) by bin(TimeGenerated, 1h)
| order by TimeGenerated asc
```

---

## Panel 5 — Total Cost USD (stat panel)

```kql
ContainerAppConsoleLogs_CL
| where ContainerName_s == "backlog-synthesizer"
| where Log_s has "pipeline_completed"
| extend d = parse_json(Log_s)
| where tostring(d.event) == "pipeline_completed"
| extend cost = todouble(d.cost_usd)
| summarize TotalCost = sum(cost), AvgCostPerRun = avg(cost)
```

---

## Panel 6 — Cost Over Time (time series)

```kql
ContainerAppConsoleLogs_CL
| where ContainerName_s == "backlog-synthesizer"
| where Log_s has "pipeline_completed"
| extend d = parse_json(Log_s)
| where tostring(d.event) == "pipeline_completed"
| extend cost = todouble(d.cost_usd)
| summarize TotalCost = sum(cost) by bin(TimeGenerated, 1h)
| order by TimeGenerated asc
```

---

## Panel 7 — Token Usage by Stage (bar chart)

```kql
ContainerAppConsoleLogs_CL
| where ContainerName_s == "backlog-synthesizer"
| where Log_s has "pipeline_completed"
| extend d = parse_json(Log_s)
| where tostring(d.event) == "pipeline_completed"
| extend
    parser_in       = toint(d.token_usage.parser.input),
    parser_out      = toint(d.token_usage.parser.output),
    story_in        = toint(d.token_usage.story_writer.input),
    story_out       = toint(d.token_usage.story_writer.output),
    epic_in         = toint(d.token_usage.epic_decomposer.input),
    epic_out        = toint(d.token_usage.epic_decomposer.output),
    gap_in          = toint(d.token_usage.gap_detector.input),
    gap_out         = toint(d.token_usage.gap_detector.output)
| summarize
    Parser          = sum(parser_in + parser_out),
    StoryWriter     = sum(story_in + story_out),
    EpicDecomposer  = sum(epic_in + epic_out),
    GapDetector     = sum(gap_in + gap_out)
```

---

## Panel 8 — Total Tokens Over Time (time series)

```kql
ContainerAppConsoleLogs_CL
| where ContainerName_s == "backlog-synthesizer"
| where Log_s has "pipeline_completed"
| extend d = parse_json(Log_s)
| where tostring(d.event) == "pipeline_completed"
| extend total_tokens = toint(d.token_usage.total.input) + toint(d.token_usage.total.output)
| summarize TotalTokens = sum(total_tokens) by bin(TimeGenerated, 1h)
| order by TimeGenerated asc
```

---

## Panel 9 — Stories and Epics Produced (table)

```kql
ContainerAppConsoleLogs_CL
| where ContainerName_s == "backlog-synthesizer"
| where Log_s has "pipeline_completed"
| extend d = parse_json(Log_s)
| where tostring(d.event) == "pipeline_completed"
| project
    TimeGenerated,
    RunID        = tostring(d.run_id),
    User         = tostring(d.user_id),
    Source       = tostring(d.source_label),
    Epics        = toint(d.epic_count),
    Stories      = toint(d.story_count),
    Duplicates   = toint(d.dup_count),
    Gaps         = toint(d.gap_count),
    Conflicts    = toint(d.conflict_count),
    Model        = tostring(d.model),
    CostUSD      = todouble(d.cost_usd),
    ElapsedSec   = todouble(d.elapsed_seconds)
| order by TimeGenerated desc
```

---

## Panel 10 — Cost Per User (bar chart)

```kql
ContainerAppConsoleLogs_CL
| where ContainerName_s == "backlog-synthesizer"
| where Log_s has "pipeline_completed"
| extend d = parse_json(Log_s)
| where tostring(d.event) == "pipeline_completed"
| extend user = tostring(d.user_id), cost = todouble(d.cost_usd)
| summarize TotalCost = sum(cost), Runs = count() by user
| order by TotalCost desc
```

---

## Panel 11 — Model Usage Distribution (pie chart)

```kql
ContainerAppConsoleLogs_CL
| where ContainerName_s == "backlog-synthesizer"
| where Log_s has "pipeline_completed"
| extend d = parse_json(Log_s)
| where tostring(d.event) == "pipeline_completed"
| extend model = tostring(d.model)
| summarize Runs = count() by model
| order by Runs desc
```

---

## Panel 12 — Error Rate (stat panel)

```kql
ContainerAppConsoleLogs_CL
| where ContainerName_s == "backlog-synthesizer"
| where Log_s has_any ("[ERROR]", "[WARNING]")
| where Log_s has_any ("failed", "error", "exception")
| summarize Errors = count() by bin(TimeGenerated, 1h)
| order by TimeGenerated asc
```

---

## Log Analytics Workspace details

- **Workspace name:** `law-backlog-synthesizer`
- **Resource group:** `rg-backlog-synthesizer`
- **Table:** `ContainerAppConsoleLogs_CL`
- **Key column:** `Log_s` — full log line including the JSON payload
- **Filter column:** `ContainerName_s == "backlog-synthesizer"`

## Grafana data source setup (Azure Monitor)

In Grafana Cloud → Connections → Add data source → Azure Monitor:

| Field | Value |
|---|---|
| Directory (tenant) ID | `AZURE_TENANT_ID` |
| Application (client) ID | `AZURE_CLIENT_ID` |
| Client secret | `AZURE_CLIENT_SECRET` |
| Default subscription | Your Azure subscription ID |

Set query type to **Logs** and workspace to `law-backlog-synthesizer` for all panels above.
