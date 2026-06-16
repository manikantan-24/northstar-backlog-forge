# ── Azure Monitor Workbook ────────────────────────────────────────────────────
# Workbooks are Microsoft's modern, fully-supported alternative to portal dashboards.
# They are version-controlled, highly visual, and run natively inside Azure Monitor
# querying your Log Analytics Workspace.

resource "random_uuid" "workbook_id" {}

resource "azurerm_application_insights_workbook" "main" {
  name                = random_uuid.workbook_id.result
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  display_name        = "Backlog Synthesizer Operations"
  source_id           = lower(azurerm_log_analytics_workspace.main.id)
  tags                = local.common_tags

  data_json = templatefile("${path.module}/templates/workbook.json.tpl", {
    workspace_id = lower(azurerm_log_analytics_workspace.main.id)
  })
}
