terraform {
  required_version = ">= 1.5"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.11"
    }
    # azuread removed — SP created manually, not via Terraform (free tier lacks AD perms)
  }

  # Remote state — backend config values passed via terraform init -backend-config in CI.
  # Storage account (stbacklogstate) is bootstrap infra, created by the workflow before init.
  backend "azurerm" {
    resource_group_name  = "rg-tfstate"
    storage_account_name = "stbacklogstate"
    container_name       = "tfstate"
    key                  = "backlog-synthesizer.tfstate"
  }
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

# ── User-Assigned Managed Identity ────────────────────────────────────────────
# The Container App uses this identity to pull secrets from Key Vault without
# any credentials appearing in Terraform state, GitHub Actions env, or logs.
resource "azurerm_user_assigned_identity" "app" {
  name                = "id-backlog-synthesizer"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.common_tags
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
# Using access policies (not RBAC) — works on free tier without Owner role.
resource "azurerm_key_vault" "main" {
  name                       = var.keyvault_name
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  enable_rbac_authorization  = false
  purge_protection_enabled   = false
  soft_delete_retention_days = 7
  tags                       = local.common_tags

  # Operator (whoever runs Terraform via GitHub Actions SP) — full secret management
  access_policy {
    tenant_id          = data.azurerm_client_config.current.tenant_id
    object_id          = data.azurerm_client_config.current.object_id
    secret_permissions = ["Get", "List", "Set", "Delete", "Purge", "Recover"]
  }

  # Container App managed identity — read only
  access_policy {
    tenant_id          = data.azurerm_client_config.current.tenant_id
    object_id          = azurerm_user_assigned_identity.app.principal_id
    secret_permissions = ["Get", "List"]
  }
}

# Wait for Key Vault access policy to propagate before writing secrets.
# Azure RBAC/access policy changes can take 15-30s to take effect.
resource "time_sleep" "kv_policy_propagation" {
  depends_on      = [azurerm_key_vault.main]
  create_duration = "30s"
}

# ── Secrets in Key Vault ───────────────────────────────────────────────────────
# All sensitive values live here. Container App reads them at runtime via MSI.
# Secret values are marked sensitive=true in variables.tf and never logged.

resource "azurerm_key_vault_secret" "anthropic_key" {
  name         = "ANTHROPIC-API-KEY"
  value        = var.anthropic_api_key
  key_vault_id  = azurerm_key_vault.main.id
  depends_on    = [time_sleep.kv_policy_propagation]
}

resource "azurerm_key_vault_secret" "google_key" {
  name         = "GOOGLE-API-KEY"
  value        = var.google_api_key
  key_vault_id  = azurerm_key_vault.main.id
  depends_on    = [time_sleep.kv_policy_propagation]
}

resource "azurerm_key_vault_secret" "jira_token" {
  name         = "JIRA-API-TOKEN"
  value        = var.jira_api_token
  key_vault_id  = azurerm_key_vault.main.id
  depends_on    = [time_sleep.kv_policy_propagation]
}

resource "azurerm_key_vault_secret" "github_token" {
  name         = "GITHUB-TOKEN"
  value        = var.github_token
  key_vault_id  = azurerm_key_vault.main.id
  depends_on    = [time_sleep.kv_policy_propagation]
}

resource "azurerm_key_vault_secret" "entra_client_secret" {
  name         = "ENTRA-CLIENT-SECRET"
  value        = var.entra_client_secret
  key_vault_id  = azurerm_key_vault.main.id
  depends_on    = [time_sleep.kv_policy_propagation]
}

resource "azurerm_key_vault_secret" "otel_headers" {
  name         = "OTEL-EXPORTER-OTLP-HEADERS"
  value        = var.otel_headers
  key_vault_id  = azurerm_key_vault.main.id
  depends_on    = [time_sleep.kv_policy_propagation]
}

resource "azurerm_key_vault_secret" "acr_password" {
  name         = "ACR-ADMIN-PASSWORD"
  value        = azurerm_container_registry.acr.admin_password
  key_vault_id  = azurerm_key_vault.main.id
  depends_on    = [time_sleep.kv_policy_propagation]
}

resource "azurerm_key_vault_secret" "auth_cookie_secret" {
  name         = "AUTH-COOKIE-SECRET"
  value        = var.auth_cookie_secret
  key_vault_id  = azurerm_key_vault.main.id
  depends_on    = [time_sleep.kv_policy_propagation]
}

resource "azurerm_key_vault_secret" "slack_webhook_url" {
  name         = "SLACK-WEBHOOK-URL"
  value        = var.slack_webhook_url
  key_vault_id  = azurerm_key_vault.main.id
  depends_on    = [time_sleep.kv_policy_propagation]
}

# ── Azure Cache for Redis ──────────────────────────────────────────────────────
# Used by budget_store.py for atomic cross-pod budget reserve/settle and
# per-user request rate limiting. Without this, budget enforcement falls back
# to per-pod file-based counting (single-pod only).
# Basic C0 (250 MB, no SLA) is sufficient for budget keys — each key is <1 KB.
resource "azurerm_redis_cache" "main" {
  name                = "redis-backlog-${var.environment}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku_name            = "Basic"
  family              = "C"
  capacity            = 0
  non_ssl_port_enabled = false
  minimum_tls_version = "1.2"
  tags                = local.common_tags
}

resource "azurerm_key_vault_secret" "redis_url" {
  name         = "REDIS-URL"
  key_vault_id = azurerm_key_vault.main.id
  # rediss:// (double-s) = TLS — Azure Redis only allows TLS on port 6380
  value      = "rediss://:${azurerm_redis_cache.main.primary_access_key}@${azurerm_redis_cache.main.hostname}:6380/0"
  depends_on = [time_sleep.kv_policy_propagation]
}

# ── Storage Account + Azure Files ─────────────────────────────────────────────
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
  quota                = 10
}

resource "azurerm_storage_share" "outputs" {
  name                 = "backlog-outputs"
  storage_account_name = azurerm_storage_account.main.name
  quota                = 50
}

# ── Log Analytics ──────────────────────────────────────────────────────────────
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

resource "azurerm_container_app_environment_storage" "data" {
  name                         = "backlog-data"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = azurerm_storage_account.main.name
  share_name                   = azurerm_storage_share.data.name
  access_key                   = azurerm_storage_account.main.primary_access_key
  access_mode                  = "ReadWrite"
}

resource "azurerm_container_app_environment_storage" "outputs" {
  name                         = "backlog-outputs"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = azurerm_storage_account.main.name
  share_name                   = azurerm_storage_share.outputs.name
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

  # Attach the managed identity so it can pull from Key Vault
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  # ── Secrets — all pulled from Key Vault via managed identity ────────────────
  # The Container App runtime fetches the secret VALUE from KV at startup.
  # Secret values never appear in Terraform plan output, state files, or logs.
  secret {
    name                = "acr-password"
    key_vault_secret_id = azurerm_key_vault_secret.acr_password.versionless_id
    identity            = azurerm_user_assigned_identity.app.id
  }
  secret {
    name                = "anthropic-api-key"
    key_vault_secret_id = azurerm_key_vault_secret.anthropic_key.versionless_id
    identity            = azurerm_user_assigned_identity.app.id
  }
  secret {
    name                = "google-api-key"
    key_vault_secret_id = azurerm_key_vault_secret.google_key.versionless_id
    identity            = azurerm_user_assigned_identity.app.id
  }
  secret {
    name                = "jira-api-token"
    key_vault_secret_id = azurerm_key_vault_secret.jira_token.versionless_id
    identity            = azurerm_user_assigned_identity.app.id
  }
  secret {
    name                = "github-token"
    key_vault_secret_id = azurerm_key_vault_secret.github_token.versionless_id
    identity            = azurerm_user_assigned_identity.app.id
  }
  secret {
    name                = "entra-client-secret"
    key_vault_secret_id = azurerm_key_vault_secret.entra_client_secret.versionless_id
    identity            = azurerm_user_assigned_identity.app.id
  }
  secret {
    name                = "otel-headers"
    key_vault_secret_id = azurerm_key_vault_secret.otel_headers.versionless_id
    identity            = azurerm_user_assigned_identity.app.id
  }
  secret {
    name                = "auth-cookie-secret"
    key_vault_secret_id = azurerm_key_vault_secret.auth_cookie_secret.versionless_id
    identity            = azurerm_user_assigned_identity.app.id
  }
  secret {
    name                = "slack-webhook-url"
    key_vault_secret_id = azurerm_key_vault_secret.slack_webhook_url.versionless_id
    identity            = azurerm_user_assigned_identity.app.id
  }
  secret {
    name                = "redis-url"
    key_vault_secret_id = azurerm_key_vault_secret.redis_url.versionless_id
    identity            = azurerm_user_assigned_identity.app.id
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
    min_replicas = 0
    max_replicas = 3

    volume {
      name         = "backlog-data"
      storage_type = "AzureFile"
      storage_name = "backlog-data"
    }

    volume {
      name         = "backlog-outputs"
      storage_type = "AzureFile"
      storage_name = "backlog-outputs"
    }

    container {
      name   = "backlog-synthesizer"
      image  = "${azurerm_container_registry.acr.login_server}/backlog-synthesizer:latest"
      cpu    = 1.0
      memory = "2Gi"

      # ── Non-secret config (plain env vars) ────────────────────────────────
      env {
        name  = "AUTH_DISABLED"
        value = "0"
      }
      env {
        name  = "LOGS_DIR"
        value = "/app/logs"
      }
      env {
        name  = "OUTPUTS_DIR"
        value = "/app/outputs"
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
        name  = "OTEL_EXPORTER_OTLP_ENDPOINT"
        value = var.otel_endpoint
      }
      env {
        name  = "ATLASSIAN_MCP_ENABLED"
        value = "1"
      }
      env {
        name  = "GITHUB_MCP_ENABLED"
        value = "1"
      }
      env {
        name  = "JIRA_BASE_URL"
        value = var.jira_base_url
      }
      env {
        name  = "JIRA_EMAIL"
        value = var.jira_email
      }
      env {
        name  = "JIRA_PROJECT_KEY"
        value = var.jira_project_key
      }
      env {
        name  = "GITHUB_OWNER"
        value = var.github_owner
      }
      env {
        name  = "GITHUB_REPO"
        value = var.github_repo
      }
      env {
        name  = "ENTRA_TENANT_ID"
        value = var.entra_tenant_id
      }
      env {
        name  = "ENTRA_CLIENT_ID"
        value = var.entra_client_id
      }
      env {
        name  = "ENTRA_TENANT_DOMAIN"
        value = var.entra_tenant_domain
      }
      env {
        name  = "ENTRA_REDIRECT_URI"
        value = var.entra_redirect_uri
      }

      # ── Secrets from Key Vault (value comes from KV via managed identity) ─
      env {
        name        = "ANTHROPIC_API_KEY"
        secret_name = "anthropic-api-key"
      }
      env {
        name        = "GOOGLE_API_KEY"
        secret_name = "google-api-key"
      }
      env {
        name        = "JIRA_API_TOKEN"
        secret_name = "jira-api-token"
      }
      env {
        name        = "GITHUB_TOKEN"
        secret_name = "github-token"
      }
      env {
        name        = "ENTRA_CLIENT_SECRET"
        secret_name = "entra-client-secret"
      }
      env {
        name        = "OTEL_EXPORTER_OTLP_HEADERS"
        secret_name = "otel-headers"
      }
      env {
        name        = "AUTH_COOKIE_SECRET"
        secret_name = "auth-cookie-secret"
      }
      env {
        name        = "SLACK_WEBHOOK_URL"
        secret_name = "slack-webhook-url"
      }
      env {
        name        = "REDIS_URL"
        secret_name = "redis-url"
      }

      volume_mounts {
        name = "backlog-data"
        path = "/app/logs"
      }

      volume_mounts {
        name = "backlog-outputs"
        path = "/app/outputs"
      }

      liveness_probe {
        transport               = "HTTP"
        port                    = 8501
        path                    = "/_stcore/health"
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }

      readiness_probe {
        transport        = "HTTP"
        port             = 8501
        path             = "/_stcore/health"
        interval_seconds = 10
        timeout          = 5
      }
    }
  }

  lifecycle {
    # image — managed by deploy.yml, not Terraform
    ignore_changes = [
      template[0].container[0].image,
    ]
  }
}

# ── Container App diagnostic setting → Log Analytics ──────────────────────────
# Routes ContainerAppConsoleLogs (stdout/stderr) explicitly to the Log Analytics
# Workspace so structured JSON emitted by the app is queryable via KQL.
# The Container App Environment already links to the same workspace, but this
# diagnostic setting guarantees console logs are captured at the app level too.
resource "azurerm_monitor_diagnostic_setting" "container_app" {
  name                       = "diag-backlog-synthesizer"
  target_resource_id         = azurerm_container_app.app.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log {
    category = "ContainerAppConsoleLogs"
  }

  enabled_log {
    category = "ContainerAppSystemLogs"
  }
}

# ── Note: GitHub Actions service principal (sp-backlog-synthesizer-terraform) ──
# Created manually in Azure Portal — not managed by Terraform to avoid
# Azure AD app registration permissions requirement on free tier accounts.
# Credentials stored as GitHub environment secrets: AZURE_CLIENT_ID / SECRET.

# ── Locals ─────────────────────────────────────────────────────────────────────
locals {
  common_tags = {
    project     = "backlog-synthesizer"
    environment = var.environment
    managed_by  = "terraform"
  }
}
