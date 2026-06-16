# ── Azure Monitor Portal Dashboard ─────────────────────────────────────────────
# This dashboard aggregates KQL queries against the Log Analytics Workspace
# to display total runs, duration, cost, errors, token usage, user activity,
# model usage, and a tabular log summary.

resource "azurerm_portal_dashboard" "main" {
  name                = "db-backlog-synthesizer-${var.environment}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.common_tags

  dashboard_properties = templatefile("${path.module}/templates/dashboard.json.tpl", {
    subscription_id     = data.azurerm_client_config.current.subscription_id
    resource_group_name = azurerm_resource_group.main.name
    workspace_name      = azurerm_log_analytics_workspace.main.name
    workspace_id        = azurerm_log_analytics_workspace.main.id
    app_url             = "https://${azurerm_container_app.app.ingress[0].fqdn}"
  })
}
