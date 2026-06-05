output "app_url" {
  description = "Public URL of the deployed Backlog Synthesizer"
  value       = "https://${azurerm_container_app.app.ingress[0].fqdn}"
}

output "acr_login_server" {
  description = "ACR login server — use as ACR_REGISTRY GitHub secret"
  value       = azurerm_container_registry.acr.login_server
}

output "resource_group_name" {
  description = "Resource group — use as AZURE_RESOURCE_GROUP GitHub secret"
  value       = azurerm_resource_group.main.name
}

output "container_app_name" {
  description = "Container App name — use as CONTAINERAPP_NAME GitHub secret"
  value       = azurerm_container_app.app.name
}

output "key_vault_uri" {
  description = "Key Vault URI — all app secrets are stored here"
  value       = azurerm_key_vault.main.vault_uri
}

output "managed_identity_client_id" {
  description = "Client ID of the Container App managed identity"
  value       = azurerm_user_assigned_identity.app.client_id
}

output "github_actions_credentials" {
  description = "AZURE_CREDENTIALS JSON for GitHub Actions secret"
  sensitive   = true
  value = jsonencode({
    clientId       = azuread_application.github_actions.client_id
    clientSecret   = azuread_service_principal_password.github_actions.value
    subscriptionId = data.azurerm_client_config.current.subscription_id
    tenantId       = data.azurerm_client_config.current.tenant_id
  })
}

output "key_vault_secrets_stored" {
  description = "Secrets stored in Key Vault (names only — values never output)"
  sensitive   = true
  value = compact([
    "ANTHROPIC-API-KEY",
    "GOOGLE-API-KEY",
    "JIRA-API-TOKEN",
    "GITHUB-TOKEN",
    "ENTRA-CLIENT-SECRET",
    "OTEL-EXPORTER-OTLP-HEADERS",
    "ACR-ADMIN-PASSWORD",
  ])
}
