{
  "version": "Notebook/1.0",
  "items": [
    {
      "type": 1,
      "content": {
        "json": "# Backlog Synthesizer Operational Workbook\nWelcome to the Azure Monitor Workbook for **Backlog Synthesizer**. This workbook provides real-time visibility into pipeline executions, cost control, model usage, and error tracking directly from your Log Analytics workspace."
      },
      "name": "welcome_text"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "ContainerAppConsoleLogs_CL\n| where ContainerName_s == \"backlog-synthesizer\"\n| where Log_s has \"pipeline_completed\"\n| extend d = parse_json(Log_s)\n| where tostring(d.event) == \"pipeline_completed\"\n| extend elapsed = todouble(d.elapsed_seconds), cost = todouble(d.cost_usd)\n| summarize \n    TotalRuns = count(),\n    AvgDurationSec = round(avg(elapsed), 1),\n    TotalCostUSD = round(sum(cost), 2)",
        "size": 3,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "crossComponentResources": [
          "${workspace_id}"
        ],
        "visualization": "tiles",
        "tileSettings": {
          "showBorder": true,
          "titleSettings": {
            "columnId": "TotalRuns",
            "formatter": 1
          }
        }
      },
      "name": "kpi_summary_tiles"
    },
    {
      "type": 1,
      "content": {
        "json": "## Pipeline Activity & Latency"
      },
      "name": "activity_section_header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "ContainerAppConsoleLogs_CL\n| where ContainerName_s == \"backlog-synthesizer\"\n| where Log_s has \"pipeline_completed\"\n| extend d = parse_json(Log_s)\n| where tostring(d.event) == \"pipeline_completed\"\n| summarize Runs = count() by bin(TimeGenerated, 1h)\n| order by TimeGenerated asc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "crossComponentResources": [
          "${workspace_id}"
        ],
        "visualization": "linechart"
      },
      "name": "runs_over_time_chart"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "ContainerAppConsoleLogs_CL\n| where ContainerName_s == \"backlog-synthesizer\"\n| where Log_s has \"pipeline_completed\"\n| extend d = parse_json(Log_s)\n| where tostring(d.event) == \"pipeline_completed\"\n| extend elapsed = todouble(d.elapsed_seconds)\n| summarize AvgDuration = avg(elapsed) by bin(TimeGenerated, 1h)\n| order by TimeGenerated asc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "crossComponentResources": [
          "${workspace_id}"
        ],
        "visualization": "linechart"
      },
      "name": "duration_over_time_chart"
    },
    {
      "type": 1,
      "content": {
        "json": "## Financials & Cost Attribution"
      },
      "name": "cost_section_header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "ContainerAppConsoleLogs_CL\n| where ContainerName_s == \"backlog-synthesizer\"\n| where Log_s has \"pipeline_completed\"\n| extend d = parse_json(Log_s)\n| where tostring(d.event) == \"pipeline_completed\"\n| extend cost = todouble(d.cost_usd)\n| summarize Cost = sum(cost) by bin(TimeGenerated, 1h)\n| order by TimeGenerated asc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "crossComponentResources": [
          "${workspace_id}"
        ],
        "visualization": "linechart"
      },
      "name": "cost_over_time_chart"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "ContainerAppConsoleLogs_CL\n| where ContainerName_s == \"backlog-synthesizer\"\n| where Log_s has \"pipeline_completed\"\n| extend d = parse_json(Log_s)\n| where tostring(d.event) == \"pipeline_completed\"\n| extend user = tostring(d.user_id), cost = todouble(d.cost_usd)\n| summarize TotalCost = round(sum(cost), 2) by user\n| order by TotalCost desc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "crossComponentResources": [
          "${workspace_id}"
        ],
        "visualization": "barchart"
      },
      "name": "cost_by_user_chart"
    },
    {
      "type": 1,
      "content": {
        "json": "## Token Usage & Model Distribution"
      },
      "name": "token_section_header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "ContainerAppConsoleLogs_CL\n| where ContainerName_s == \"backlog-synthesizer\"\n| where Log_s has \"pipeline_completed\"\n| extend d = parse_json(Log_s)\n| where tostring(d.event) == \"pipeline_completed\"\n| extend parser_in = toint(d.token_usage.parser.input), parser_out = toint(d.token_usage.parser.output), story_in = toint(d.token_usage.story_writer.input), story_out = toint(d.token_usage.story_writer.output), epic_in = toint(d.token_usage.epic_decomposer.input), epic_out = toint(d.token_usage.epic_decomposer.output), gap_in = toint(d.token_usage.gap_detector.input), gap_out = toint(d.token_usage.gap_detector.output)\n| summarize Parser = sum(parser_in + parser_out), StoryWriter = sum(story_in + story_out), EpicDecomposer = sum(epic_in + epic_out), GapDetector = sum(gap_in + gap_out)",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "crossComponentResources": [
          "${workspace_id}"
        ],
        "visualization": "barchart"
      },
      "name": "token_usage_by_stage_chart"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "ContainerAppConsoleLogs_CL\n| where ContainerName_s == \"backlog-synthesizer\"\n| where Log_s has \"pipeline_completed\"\n| extend d = parse_json(Log_s)\n| where tostring(d.event) == \"pipeline_completed\"\n| extend model = tostring(d.model)\n| summarize Runs = count() by model",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "crossComponentResources": [
          "${workspace_id}"
        ],
        "visualization": "piechart"
      },
      "name": "model_distribution_chart"
    },
    {
      "type": 1,
      "content": {
        "json": "## Executed Run Log"
      },
      "name": "log_section_header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "ContainerAppConsoleLogs_CL\n| where ContainerName_s == \"backlog-synthesizer\"\n| where Log_s has \"pipeline_completed\"\n| extend d = parse_json(Log_s)\n| where tostring(d.event) == \"pipeline_completed\"\n| project TimeGenerated, RunID = tostring(d.run_id), User = tostring(d.user_id), Epics = toint(d.epic_count), Stories = toint(d.story_count), Gaps = toint(d.gap_count), Conflicts = toint(d.conflict_count), Model = tostring(d.model), CostUSD = round(todouble(d.cost_usd), 3), ElapsedSec = round(todouble(d.elapsed_seconds), 1)\n| order by TimeGenerated desc\n| limit 50",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "crossComponentResources": [
          "${workspace_id}"
        ],
        "visualization": "table"
      },
      "name": "recent_runs_table"
    }
  ],
  "isLocked": false
}
