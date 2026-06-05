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

variable "resource_group_name" {
  type    = string
  default = "rg-backlog-synthesizer"
}

variable "acr_name" {
  description = "Azure Container Registry name — must be globally unique, lowercase, no hyphens"
  type        = string
  default     = "backlogsynth"
}

variable "keyvault_name" {
  description = "Key Vault name — must be globally unique, 3-24 chars"
  type        = string
  default     = "kv-backlog-synth"
}

variable "storage_account_name" {
  description = "Storage account name — globally unique, lowercase, no hyphens, max 24 chars"
  type        = string
  default     = "stbacklogsynth"
}

variable "container_app_env_name" {
  type    = string
  default = "cae-backlog-synthesizer"
}

variable "container_app_name" {
  type    = string
  default = "backlog-synthesizer"
}

# ── Secrets — supply via terraform.tfvars or TF_VAR_* env vars ────────────────
# Never hard-code these here.

variable "anthropic_api_key" {
  description = "Anthropic API key (required)"
  type        = string
  sensitive   = true
}

variable "google_api_key" {
  description = "Google API key for Gemini models (optional)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "jira_api_token" {
  description = "Atlassian API token for live Jira integration (optional)"
  type        = string
  sensitive   = true
  default     = ""
}
