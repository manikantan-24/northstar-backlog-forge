terraform {
  required_version = ">= 1.5"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 2.50"
    }
  }

  # Store Terraform state in Azure Blob Storage so the team shares one truth.
  # Uncomment and fill in after running `az storage account create` for state.
  # backend "azurerm" {
  #   resource_group_name  = "rg-tfstate"
  #   storage_account_name = "stbacklogsynth"
  #   container_name       = "tfstate"
  #   key                  = "backlog-synthesizer.tfstate"
  # }
}

provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy    = false
      recover_soft_deleted_key_vaults = true
    }
  }
}

data "azurerm_client_config" "current" {}

# ── Resource Group ─────────────────────────────────────────────────────────────
resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.common_tags
}

# ── Azure Container Registry ───────────────────────────────────────────────────
resource "azurerm_container_registry" "acr" {
  name                = var.acr_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic"
  admin_enabled       = true
  tags                = local.common_tags
}

# ── Azure Key Vault ────────────────────────────────────────────────────────────
resource "azurerm_key_vault" "main" {
  name                       = var.keyvault_name
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  enable_rbac_authorization  = true
  purge_protection_enabled   = false
  soft_delete_retention_days = 7
  tags                       = local.common_tags
}

# Grant the operator (whoever runs terraform) Key Vault Secrets Officer.
resource "azurerm_role_assignment" "kv_operator" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Secrets — values supplied via terraform.tfvars or env vars (TF_VAR_*)
resource "azurerm_key_vault_secret" "anthropic_key" {
  name         = "ANTHROPIC-API-KEY"
  value        = var.anthropic_api_key
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.kv_operator]
}

resource "azurerm_key_vault_secret" "google_key" {
  count        = var.google_api_key != "" ? 1 : 0
  name         = "GOOGLE-API-KEY"
  value        = var.google_api_key
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.kv_operator]
}

resource "azurerm_key_vault_secret" "jira_token" {
  count        = var.jira_api_token != "" ? 1 : 0
  name         = "JIRA-API-TOKEN"
  value        = var.jira_api_token
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.kv_operator]
}

# ── Storage Account + Azure Files (persistent logs/outputs) ───────────────────
resource "azurerm_storage_account" "main" {
  name                     = var.storage_account_name
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  tags                     = local.common_tags
}

resource "azurerm_storage_share" "data" {
  name                 = "backlog-data"
  storage_account_name = azurerm_storage_account.main.name
  quota                = 10  # GB
}

# ── Log Analytics (required by Container Apps environment) ────────────────────
resource "azurerm_log_analytics_workspace" "main" {
  name                = "law-backlog-synthesizer"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.common_tags
}

# ── Container Apps Environment ─────────────────────────────────────────────────
resource "azurerm_container_app_environment" "main" {
  name                       = var.container_app_env_name
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.common_tags
}

# Mount Azure Files into the Container Apps environment for persistent storage
resource "azurerm_container_app_environment_storage" "data" {
  name                         = "backlog-data"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = azurerm_storage_account.main.name
  share_name                   = azurerm_storage_share.data.name
  access_key                   = azurerm_storage_account.main.primary_access_key
  access_mode                  = "ReadWrite"
}

# ── Container App ──────────────────────────────────────────────────────────────
resource "azurerm_container_app" "app" {
  name                         = var.container_app_name
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  tags                         = local.common_tags

  secret {
    name  = "acr-password"
    value = azurerm_container_registry.acr.admin_password
  }
  secret {
    name  = "anthropic-api-key"
    value = var.anthropic_api_key
  }

  registry {
    server               = azurerm_container_registry.acr.login_server
    username             = azurerm_container_registry.acr.admin_username
    password_secret_name = "acr-password"
  }

  ingress {
    external_enabled = true
    target_port      = 8501
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas = 0   # scale to zero when idle
    max_replicas = 3

    volume {
      name         = "backlog-data"
      storage_type = "AzureFile"
      storage_name = "backlog-data"
    }

    container {
      name   = "backlog-synthesizer"
      image  = "${azurerm_container_registry.acr.login_server}/backlog-synthesizer:latest"
      cpu    = 1.0
      memory = "2Gi"

      env {
        name  = "AUTH_DISABLED"
        value = "0"
      }
      env {
        name        = "ANTHROPIC_API_KEY"
        secret_name = "anthropic-api-key"
      }
      env {
        name  = "OTEL_ENABLED"
        value = "1"
      }
      env {
        name  = "OTEL_SERVICE_NAME"
        value = "backlog-synthesizer"
      }
      env {
        name  = "ATLASSIAN_MCP_ENABLED"
        value = "1"
      }
      env {
        name  = "GITHUB_MCP_ENABLED"
        value = "1"
      }

      volume_mounts {
        name = "backlog-data"
        path = "/app/logs"
      }

      liveness_probe {
        transport = "HTTP"
        port      = 8501
        path      = "/_stcore/health"
        period_seconds    = 30
        timeout_seconds   = 5
        failure_count_threshold = 3
      }

      readiness_probe {
        transport = "HTTP"
        port      = 8501
        path      = "/_stcore/health"
        period_seconds = 10
        timeout_seconds = 5
      }
    }
  }

  lifecycle {
    # Image tag is managed by GitHub Actions on every deploy.
    # Prevent Terraform from resetting it to "latest" after the first deploy.
    ignore_changes = [template[0].container[0].image]
  }
}

# ── Service Principal for GitHub Actions ──────────────────────────────────────
resource "azuread_application" "github_actions" {
  display_name = "sp-backlog-synthesizer-ghactions"
}

resource "azuread_service_principal" "github_actions" {
  client_id = azuread_application.github_actions.client_id
}

resource "azuread_service_principal_password" "github_actions" {
  service_principal_id = azuread_service_principal.github_actions.id
  end_date_relative    = "8760h"  # 1 year
}

resource "azurerm_role_assignment" "github_contributor" {
  scope                = azurerm_resource_group.main.id
  role_definition_name = "Contributor"
  principal_id         = azuread_service_principal.github_actions.object_id
}

resource "azurerm_role_assignment" "github_acr_push" {
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPush"
  principal_id         = azuread_service_principal.github_actions.object_id
}

# ── Locals ─────────────────────────────────────────────────────────────────────
locals {
  common_tags = {
    project     = "backlog-synthesizer"
    environment = var.environment
    managed_by  = "terraform"
  }
}
