variable "location" {
  description = "Azure region"
  type        = string
  default     = "eastus"
}

variable "environment" {
  description = "Deployment environment (staging / production)"
  type        = string
  default     = "staging"
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "Must be staging or production."
  }
}

variable "resource_group_name"    { type = string; default = "rg-backlog-synthesizer" }
variable "acr_name"               { description = "Azure Container Registry name (globally unique, lowercase)"; type = string; default = "backlogsynth" }
variable "keyvault_name"          { description = "Key Vault name (globally unique, 3-24 chars)"; type = string; default = "kv-backlog-synth" }
variable "storage_account_name"   { description = "Storage account name (globally unique, lowercase, max 24 chars)"; type = string; default = "stbacklogsynth" }
variable "container_app_env_name" { type = string; default = "cae-backlog-synthesizer" }
variable "container_app_name"     { type = string; default = "backlog-synthesizer" }

# ── Non-secret configuration ───────────────────────────────────────────────────
variable "jira_base_url"    { type = string; default = "" }
variable "jira_email"       { type = string; default = "" }
variable "jira_project_key" { type = string; default = "" }
variable "entra_tenant_id"  { type = string; default = "" }
variable "entra_client_id"  { type = string; default = "" }
variable "entra_tenant_domain" { type = string; default = "" }
variable "github_owner"     { type = string; default = "" }
variable "github_repo"      { type = string; default = "" }
variable "otel_endpoint"    { type = string; default = "" }

# ── Secrets — stored in Key Vault, never in state as plaintext ────────────────
# Supply via terraform.tfvars (gitignored) or TF_VAR_* environment variables.
# These are written to Key Vault once; Container App pulls them via Managed Identity.

variable "anthropic_api_key" {
  description = "Anthropic API key (required)"
  type        = string
  sensitive   = true
}

variable "google_api_key" {
  description = "Google API key for Gemini models"
  type        = string
  sensitive   = true
  default     = ""
}

variable "jira_api_token" {
  description = "Atlassian API token"
  type        = string
  sensitive   = true
  default     = ""
}

variable "github_token" {
  description = "GitHub PAT for MCP server"
  type        = string
  sensitive   = true
  default     = ""
}

variable "entra_client_secret" {
  description = "Microsoft Entra ID app client secret"
  type        = string
  sensitive   = true
  default     = ""
}

variable "otel_headers" {
  description = "OTEL_EXPORTER_OTLP_HEADERS (e.g. Authorization=Basic ...)"
  type        = string
  sensitive   = true
  default     = ""
}
