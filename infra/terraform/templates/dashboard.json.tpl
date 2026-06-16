{
  "lenses": {
    "0": {
      "order": 0,
      "parts": {
        "0": {
          "position": {
            "x": 0,
            "y": 0,
            "colSpan": 12,
            "rowSpan": 2
          },
          "metadata": {
            "inputs": [],
            "type": "Extension/HubsExtension/PartType/MarkdownPart",
            "settings": {
              "content": {
                "settings": {
                  "title": "Backlog Synthesizer Operational Dashboard",
                  "content": "### Backlog Synthesizer Operations & Cost Control\nWelcome to the operational dashboard for **Backlog Synthesizer**. This dashboard displays runs, execution times, costs, token consumption, and error rates parsed from container logs in real time. \n\n* **App FQDN:** `${app_url}`\n* **Resource Group:** `${resource_group_name}`\n* **Log Workspace:** `${workspace_name}`"
                }
              }
            }
          }
        },
        "1": {
          "position": {
            "x": 0,
            "y": 2,
            "colSpan": 3,
            "rowSpan": 3
          },
          "metadata": {
            "inputs": [
              { "name": "resourceId", "value": "${workspace_id}" },
              { "name": "query", "value": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | summarize TotalRuns = count()" },
              { "name": "PartTitle", "value": "Total Pipeline Runs" },
              { "name": "ControlType", "value": "FrameControlChart" },
              { "name": "SpecificChart", "value": "SingleValue" }
            ],
            "type": "Extension/Microsoft_OperationsManagementSuite_Workspace/PartType/LogsDashboardPart",
            "settings": {
              "content": {
                "Query": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | summarize TotalRuns = count()",
                "ControlType": "FrameControlChart",
                "SpecificChart": "SingleValue",
                "PartTitle": "Total Pipeline Runs"
              }
            }
          }
        },
        "2": {
          "position": {
            "x": 3,
            "y": 2,
            "colSpan": 3,
            "rowSpan": 3
          },
          "metadata": {
            "inputs": [
              { "name": "resourceId", "value": "${workspace_id}" },
              { "name": "query", "value": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | extend elapsed = todouble(d.elapsed_seconds) | summarize AvgDuration = round(avg(elapsed), 1)" },
              { "name": "PartTitle", "value": "Avg Run Duration (sec)" },
              { "name": "ControlType", "value": "FrameControlChart" },
              { "name": "SpecificChart", "value": "SingleValue" }
            ],
            "type": "Extension/Microsoft_OperationsManagementSuite_Workspace/PartType/LogsDashboardPart",
            "settings": {
              "content": {
                "Query": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | extend elapsed = todouble(d.elapsed_seconds) | summarize AvgDuration = round(avg(elapsed), 1)",
                "ControlType": "FrameControlChart",
                "SpecificChart": "SingleValue",
                "PartTitle": "Avg Run Duration (sec)"
              }
            }
          }
        },
        "3": {
          "position": {
            "x": 6,
            "y": 2,
            "colSpan": 3,
            "rowSpan": 3
          },
          "metadata": {
            "inputs": [
              { "name": "resourceId", "value": "${workspace_id}" },
              { "name": "query", "value": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | extend cost = todouble(d.cost_usd) | summarize TotalCost = round(sum(cost), 2)" },
              { "name": "PartTitle", "value": "Total LLM Cost (USD)" },
              { "name": "ControlType", "value": "FrameControlChart" },
              { "name": "SpecificChart", "value": "SingleValue" }
            ],
            "type": "Extension/Microsoft_OperationsManagementSuite_Workspace/PartType/LogsDashboardPart",
            "settings": {
              "content": {
                "Query": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | extend cost = todouble(d.cost_usd) | summarize TotalCost = round(sum(cost), 2)",
                "ControlType": "FrameControlChart",
                "SpecificChart": "SingleValue",
                "PartTitle": "Total LLM Cost (USD)"
              }
            }
          }
        },
        "4": {
          "position": {
            "x": 9,
            "y": 2,
            "colSpan": 3,
            "rowSpan": 3
          },
          "metadata": {
            "inputs": [
              { "name": "resourceId", "value": "${workspace_id}" },
              { "name": "query", "value": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has_any (\"[ERROR]\", \"[WARNING]\") | where Log_s has_any (\"failed\", \"error\", \"exception\") | summarize TotalErrors = count()" },
              { "name": "PartTitle", "value": "Total Error Count" },
              { "name": "ControlType", "value": "FrameControlChart" },
              { "name": "SpecificChart", "value": "SingleValue" }
            ],
            "type": "Extension/Microsoft_OperationsManagementSuite_Workspace/PartType/LogsDashboardPart",
            "settings": {
              "content": {
                "Query": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has_any (\"[ERROR]\", \"[WARNING]\") | where Log_s has_any (\"failed\", \"error\", \"exception\") | summarize TotalErrors = count()",
                "ControlType": "FrameControlChart",
                "SpecificChart": "SingleValue",
                "PartTitle": "Total Error Count"
              }
            }
          }
        },
        "5": {
          "position": {
            "x": 0,
            "y": 5,
            "colSpan": 6,
            "rowSpan": 4
          },
          "metadata": {
            "inputs": [
              { "name": "resourceId", "value": "${workspace_id}" },
              { "name": "query", "value": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | summarize Runs = count() by bin(TimeGenerated, 1h) | order by TimeGenerated asc" },
              { "name": "PartTitle", "value": "Pipeline Runs Over Time (Hourly)" },
              { "name": "ControlType", "value": "FrameControlChart" },
              { "name": "SpecificChart", "value": "Line" }
            ],
            "type": "Extension/Microsoft_OperationsManagementSuite_Workspace/PartType/LogsDashboardPart",
            "settings": {
              "content": {
                "Query": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | summarize Runs = count() by bin(TimeGenerated, 1h) | order by TimeGenerated asc",
                "ControlType": "FrameControlChart",
                "SpecificChart": "Line",
                "PartTitle": "Pipeline Runs Over Time (Hourly)"
              }
            }
          }
        },
        "6": {
          "position": {
            "x": 6,
            "y": 5,
            "colSpan": 6,
            "rowSpan": 4
          },
          "metadata": {
            "inputs": [
              { "name": "resourceId", "value": "${workspace_id}" },
              { "name": "query", "value": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | extend elapsed = todouble(d.elapsed_seconds) | summarize AvgDuration = avg(elapsed) by bin(TimeGenerated, 1h) | order by TimeGenerated asc" },
              { "name": "PartTitle", "value": "Avg Run Duration Over Time" },
              { "name": "ControlType", "value": "FrameControlChart" },
              { "name": "SpecificChart", "value": "Line" }
            ],
            "type": "Extension/Microsoft_OperationsManagementSuite_Workspace/PartType/LogsDashboardPart",
            "settings": {
              "content": {
                "Query": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | extend elapsed = todouble(d.elapsed_seconds) | summarize AvgDuration = avg(elapsed) by bin(TimeGenerated, 1h) | order by TimeGenerated asc",
                "ControlType": "FrameControlChart",
                "SpecificChart": "Line",
                "PartTitle": "Avg Run Duration Over Time"
              }
            }
          }
        },
        "7": {
          "position": {
            "x": 0,
            "y": 9,
            "colSpan": 6,
            "rowSpan": 4
          },
          "metadata": {
            "inputs": [
              { "name": "resourceId", "value": "${workspace_id}" },
              { "name": "query", "value": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | extend cost = todouble(d.cost_usd) | summarize TotalCost = sum(cost) by bin(TimeGenerated, 1h) | order by TimeGenerated asc" },
              { "name": "PartTitle", "value": "LLM Cost Over Time" },
              { "name": "ControlType", "value": "FrameControlChart" },
              { "name": "SpecificChart", "value": "Line" }
            ],
            "type": "Extension/Microsoft_OperationsManagementSuite_Workspace/PartType/LogsDashboardPart",
            "settings": {
              "content": {
                "Query": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | extend cost = todouble(d.cost_usd) | summarize TotalCost = sum(cost) by bin(TimeGenerated, 1h) | order by TimeGenerated asc",
                "ControlType": "FrameControlChart",
                "SpecificChart": "Line",
                "PartTitle": "LLM Cost Over Time"
              }
            }
          }
        },
        "8": {
          "position": {
            "x": 6,
            "y": 9,
            "colSpan": 6,
            "rowSpan": 4
          },
          "metadata": {
            "inputs": [
              { "name": "resourceId", "value": "${workspace_id}" },
              { "name": "query", "value": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | extend total_tokens = toint(d.token_usage.total.input) + toint(d.token_usage.total.output) | summarize TotalTokens = sum(total_tokens) by bin(TimeGenerated, 1h) | order by TimeGenerated asc" },
              { "name": "PartTitle", "value": "Total Tokens Consumed Over Time" },
              { "name": "ControlType", "value": "FrameControlChart" },
              { "name": "SpecificChart", "value": "Line" }
            ],
            "type": "Extension/Microsoft_OperationsManagementSuite_Workspace/PartType/LogsDashboardPart",
            "settings": {
              "content": {
                "Query": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | extend total_tokens = toint(d.token_usage.total.input) + toint(d.token_usage.total.output) | summarize TotalTokens = sum(total_tokens) by bin(TimeGenerated, 1h) | order by TimeGenerated asc",
                "ControlType": "FrameControlChart",
                "SpecificChart": "Line",
                "PartTitle": "Total Tokens Consumed Over Time"
              }
            }
          }
        },
        "9": {
          "position": {
            "x": 0,
            "y": 13,
            "colSpan": 6,
            "rowSpan": 4
          },
          "metadata": {
            "inputs": [
              { "name": "resourceId", "value": "${workspace_id}" },
              { "name": "query", "value": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | extend parser_in = toint(d.token_usage.parser.input), parser_out = toint(d.token_usage.parser.output), story_in = toint(d.token_usage.story_writer.input), story_out = toint(d.token_usage.story_writer.output), epic_in = toint(d.token_usage.epic_decomposer.input), epic_out = toint(d.token_usage.epic_decomposer.output), gap_in = toint(d.token_usage.gap_detector.input), gap_out = toint(d.token_usage.gap_detector.output) | summarize Parser = sum(parser_in + parser_out), StoryWriter = sum(story_in + story_out), EpicDecomposer = sum(epic_in + epic_out), GapDetector = sum(gap_in + gap_out)" },
              { "name": "PartTitle", "value": "Token Usage by Processing Stage" },
              { "name": "ControlType", "value": "FrameControlChart" },
              { "name": "SpecificChart", "value": "UnstackedBar" }
            ],
            "type": "Extension/Microsoft_OperationsManagementSuite_Workspace/PartType/LogsDashboardPart",
            "settings": {
              "content": {
                "Query": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | extend parser_in = toint(d.token_usage.parser.input), parser_out = toint(d.token_usage.parser.output), story_in = toint(d.token_usage.story_writer.input), story_out = toint(d.token_usage.story_writer.output), epic_in = toint(d.token_usage.epic_decomposer.input), epic_out = toint(d.token_usage.epic_decomposer.output), gap_in = toint(d.token_usage.gap_detector.input), gap_out = toint(d.token_usage.gap_detector.output) | summarize Parser = sum(parser_in + parser_out), StoryWriter = sum(story_in + story_out), EpicDecomposer = sum(epic_in + epic_out), GapDetector = sum(gap_in + gap_out)",
                "ControlType": "FrameControlChart",
                "SpecificChart": "UnstackedBar",
                "PartTitle": "Token Usage by Processing Stage"
              }
            }
          }
        },
        "10": {
          "position": {
            "x": 6,
            "y": 13,
            "colSpan": 6,
            "rowSpan": 4
          },
          "metadata": {
            "inputs": [
              { "name": "resourceId", "value": "${workspace_id}" },
              { "name": "query", "value": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | extend user = tostring(d.user_id), cost = todouble(d.cost_usd) | summarize TotalCost = sum(cost), Runs = count() by user | order by TotalCost desc" },
              { "name": "PartTitle", "value": "Cost and Runs by User" },
              { "name": "ControlType", "value": "FrameControlChart" },
              { "name": "SpecificChart", "value": "UnstackedBar" }
            ],
            "type": "Extension/Microsoft_OperationsManagementSuite_Workspace/PartType/LogsDashboardPart",
            "settings": {
              "content": {
                "Query": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | extend user = tostring(d.user_id), cost = todouble(d.cost_usd) | summarize TotalCost = sum(cost), Runs = count() by user | order by TotalCost desc",
                "ControlType": "FrameControlChart",
                "SpecificChart": "UnstackedBar",
                "PartTitle": "Cost and Runs by User"
              }
            }
          }
        },
        "11": {
          "position": {
            "x": 0,
            "y": 17,
            "colSpan": 4,
            "rowSpan": 4
          },
          "metadata": {
            "inputs": [
              { "name": "resourceId", "value": "${workspace_id}" },
              { "name": "query", "value": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | extend model = tostring(d.model) | summarize Runs = count() by model" },
              { "name": "PartTitle", "value": "Model Distribution" },
              { "name": "ControlType", "value": "FrameControlChart" },
              { "name": "SpecificChart", "value": "Pie" }
            ],
            "type": "Extension/Microsoft_OperationsManagementSuite_Workspace/PartType/LogsDashboardPart",
            "settings": {
              "content": {
                "Query": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | extend model = tostring(d.model) | summarize Runs = count() by model",
                "ControlType": "FrameControlChart",
                "SpecificChart": "Pie",
                "PartTitle": "Model Distribution"
              }
            }
          }
        },
        "12": {
          "position": {
            "x": 4,
            "y": 17,
            "colSpan": 8,
            "rowSpan": 4
          },
          "metadata": {
            "inputs": [
              { "name": "resourceId", "value": "${workspace_id}" },
              { "name": "query", "value": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | project TimeGenerated, RunID = tostring(d.run_id), User = tostring(d.user_id), Epics = toint(d.epic_count), Stories = toint(d.story_count), Gaps = toint(d.gap_count), Conflicts = toint(d.conflict_count), Model = tostring(d.model), CostUSD = round(todouble(d.cost_usd), 3), ElapsedSec = round(todouble(d.elapsed_seconds), 1) | order by TimeGenerated desc | limit 50" },
              { "name": "PartTitle", "value": "Recent Runs Summary" },
              { "name": "ControlType", "value": "AnalyticsGrid" }
            ],
            "type": "Extension/Microsoft_OperationsManagementSuite_Workspace/PartType/LogsDashboardPart",
            "settings": {
              "content": {
                "Query": "ContainerAppConsoleLogs_CL | where ContainerName_s == \"backlog-synthesizer\" | where Log_s has \"pipeline_completed\" | extend d = parse_json(Log_s) | where tostring(d.event) == \"pipeline_completed\" | project TimeGenerated, RunID = tostring(d.run_id), User = tostring(d.user_id), Epics = toint(d.epic_count), Stories = toint(d.story_count), Gaps = toint(d.gap_count), Conflicts = toint(d.conflict_count), Model = tostring(d.model), CostUSD = round(todouble(d.cost_usd), 3), ElapsedSec = round(todouble(d.elapsed_seconds), 1) | order by TimeGenerated desc | limit 50",
                "ControlType": "AnalyticsGrid",
                "PartTitle": "Recent Runs Summary"
              }
            }
          }
        }
      }
    }
  },
  "metadata": {
    "model": {
      "timeRange": {
        "value": {
          "relative": {
            "duration": 24,
            "timeUnit": 1
          }
        },
        "type": "MsPortalFx.Composition.Configuration.ValueTypes.TimeRange"
      }
    }
  }
}
