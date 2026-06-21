#!/usr/bin/env bash
# =============================================================================
# Backlog Synthesizer — One-time Azure resource provisioning
#
# Run this ONCE to create all Azure infrastructure. After this, GitHub Actions
# handles all deployments automatically on push to main.
#
# Prerequisites:
#   - Azure CLI installed and logged in:  az login
#   - Sufficient permissions (Contributor + Key Vault Officer on the subscription)
#
# Usage:
#   chmod +x scripts/azure_setup.sh
#   ./scripts/azure_setup.sh
#
# What it creates:
#   - Resource group
#   - Azure Container Registry (stores Docker images)
#   - Azure Key Vault (stores secrets — replaces .env in production)
#   - Azure Container Apps environment + app
#   - Azure Files share (persistent storage for logs/ outputs/ config/)
#   - Service principal for GitHub Actions
#   - Prints all GitHub secrets you need to set
# =============================================================================

set -euo pipefail

# ── Configuration — edit these before running ─────────────────────────────────
LOCATION="eastus"
RESOURCE_GROUP="rg-backlog-synthesizer"
ACR_NAME="backlogsynth"                          # must be globally unique, lowercase
KEYVAULT_NAME="kv-backlog-synth"                 # must be globally unique
CONTAINERAPP_ENV="cae-backlog-synthesizer"
CONTAINERAPP_NAME="backlog-synthesizer"
STORAGE_ACCOUNT="stbacklogsynth"                 # globally unique, lowercase, no hyphens
FILE_SHARE_NAME="backlog-data"
GITHUB_REPO="your-org/backlog-synthesizer"       # ← update to your GitHub repo

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }

SUBSCRIPTION=$(az account show --query id -o tsv)
info "Using subscription: $SUBSCRIPTION"

# ── 1. Resource group ─────────────────────────────────────────────────────────
info "Creating resource group: $RESOURCE_GROUP in $LOCATION"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none
ok "Resource group ready"

# ── 2. Azure Container Registry ───────────────────────────────────────────────
info "Creating Container Registry: $ACR_NAME"
az acr create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACR_NAME" \
  --sku Basic \
  --admin-enabled true \
  --output none
ok "ACR created: ${ACR_NAME}.azurecr.io"

# ── 3. Azure Key Vault ────────────────────────────────────────────────────────
info "Creating Key Vault: $KEYVAULT_NAME"
az keyvault create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$KEYVAULT_NAME" \
  --location "$LOCATION" \
  --enable-rbac-authorization true \
  --output none
ok "Key Vault created"

# ── 4. Store secrets in Key Vault ─────────────────────────────────────────────
warn "Enter your API keys for Key Vault storage."
warn "These will be mounted as environment variables in the container."
echo ""

read -rsp "ANTHROPIC_API_KEY (required): " ANTHROPIC_KEY; echo
read -rsp "GOOGLE_API_KEY (optional, press Enter to skip): " GOOGLE_KEY; echo
read -rsp "JIRA_API_TOKEN (optional, press Enter to skip): " JIRA_TOKEN; echo

KV_ID=$(az keyvault show --name "$KEYVAULT_NAME" --query id -o tsv)
CURRENT_USER=$(az ad signed-in-user show --query id -o tsv 2>/dev/null || echo "")

if [ -n "$CURRENT_USER" ]; then
  az role assignment create \
    --role "Key Vault Secrets Officer" \
    --assignee "$CURRENT_USER" \
    --scope "$KV_ID" \
    --output none
fi

az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "ANTHROPIC-API-KEY"    --value "$ANTHROPIC_KEY"    --output none
[ -n "$GOOGLE_KEY"  ] && az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "GOOGLE-API-KEY"       --value "$GOOGLE_KEY"   --output none
[ -n "$JIRA_TOKEN"  ] && az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "JIRA-API-TOKEN"        --value "$JIRA_TOKEN"   --output none
ok "Secrets stored in Key Vault"

# ── 5. Storage account + Azure Files (persistent volumes) ─────────────────────
info "Creating storage account: $STORAGE_ACCOUNT"
az storage account create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$STORAGE_ACCOUNT" \
  --sku Standard_LRS \
  --kind StorageV2 \
  --location "$LOCATION" \
  --output none

STORAGE_KEY=$(az storage account keys list \
  --resource-group "$RESOURCE_GROUP" \
  --account-name "$STORAGE_ACCOUNT" \
  --query "[0].value" -o tsv)

az storage share create \
  --account-name "$STORAGE_ACCOUNT" \
  --account-key "$STORAGE_KEY" \
  --name "$FILE_SHARE_NAME" \
  --quota 10 \
  --output none
ok "Azure Files share ready: $FILE_SHARE_NAME"

# ── 6. Container Apps environment ─────────────────────────────────────────────
info "Creating Container Apps environment: $CONTAINERAPP_ENV"
az containerapp env create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINERAPP_ENV" \
  --location "$LOCATION" \
  --output none

# Mount Azure Files into the environment for persistent storage
az containerapp env storage set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINERAPP_ENV" \
  --storage-name backlog-data \
  --azure-file-account-name "$STORAGE_ACCOUNT" \
  --azure-file-account-key "$STORAGE_KEY" \
  --azure-file-share-name "$FILE_SHARE_NAME" \
  --access-mode ReadWrite \
  --output none
ok "Container Apps environment created with Azure Files mount"

# ── 7. Initial Container App deployment ───────────────────────────────────────
info "Creating Container App: $CONTAINERAPP_NAME (initial deployment from mcr.microsoft.com/azuredocs/containerapps-helloworld)"
# We deploy a placeholder image first; GitHub Actions will replace it on the
# first push to main. This avoids a chicken-and-egg problem where the app
# doesn't exist yet when GitHub Actions tries to update it.

ANTHROPIC_SECRET_URI="https://${KEYVAULT_NAME}.vault.azure.net/secrets/ANTHROPIC-API-KEY/"

az containerapp create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINERAPP_NAME" \
  --environment "$CONTAINERAPP_ENV" \
  --image "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest" \
  --target-port 8501 \
  --ingress external \
  --min-replicas 0 \
  --max-replicas 3 \
  --cpu 1.0 \
  --memory 2.0Gi \
  --env-vars \
    "AUTH_DISABLED=0" \
    "USE_CHROMADB=1" \
    "OTEL_ENABLED=1" \
    "OTEL_SERVICE_NAME=backlog-synthesizer" \
  --output none

ok "Container App created (placeholder image — GitHub Actions will deploy the real image)"

APP_URL=$(az containerapp show \
  --name "$CONTAINERAPP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" \
  -o tsv)
ok "App URL: https://${APP_URL}"

# ── 8. Service principal for GitHub Actions ────────────────────────────────────
info "Creating service principal for GitHub Actions: sp-backlog-synthesizer-ghactions"

SP_JSON=$(az ad sp create-for-rbac \
  --name "sp-backlog-synthesizer-ghactions" \
  --role Contributor \
  --scopes "/subscriptions/${SUBSCRIPTION}/resourceGroups/${RESOURCE_GROUP}" \
  --sdk-auth)

# Grant the SP access to push to ACR
ACR_ID=$(az acr show --name "$ACR_NAME" --query id -o tsv)
SP_CLIENT_ID=$(echo "$SP_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['clientId'])")
az role assignment create \
  --role AcrPush \
  --assignee "$SP_CLIENT_ID" \
  --scope "$ACR_ID" \
  --output none

ok "Service principal created"

# ── 9. Print GitHub Secrets ────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Setup complete! Add these secrets to your GitHub repo:    ${NC}"
echo -e "${GREEN}  $GITHUB_REPO → Settings → Secrets → Actions             ${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${CYAN}Secret name               Value${NC}"
echo "──────────────────────────────────────────────────────────────"
echo "AZURE_CREDENTIALS         (paste the JSON block below)"
echo ""
echo "$SP_JSON"
echo ""
echo "──────────────────────────────────────────────────────────────"
echo "ACR_REGISTRY              ${ACR_NAME}.azurecr.io"
echo "AZURE_RESOURCE_GROUP      ${RESOURCE_GROUP}"
echo "CONTAINERAPP_NAME         ${CONTAINERAPP_NAME}"
echo "──────────────────────────────────────────────────────────────"
echo ""
echo -e "${YELLOW}Optional (for live Jira/Confluence):${NC}"
echo "JIRA_BASE_URL             https://your-tenant.atlassian.net"
echo "JIRA_EMAIL                you@company.com"
echo "JIRA_PROJECT_KEY          YOURPROJ"
echo "GOOGLE_API_KEY            (your Google AI key for Gemini)"
echo ""
echo -e "${GREEN}Next step: push a commit to main to trigger your first real deployment.${NC}"
echo -e "${GREEN}App will be live at: https://${APP_URL}${NC}"
echo ""
