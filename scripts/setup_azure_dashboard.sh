#!/usr/bin/env bash
# =============================================================================
# Backlog Synthesizer — Azure Monitor Portal Dashboard Setup
#
# Run this script to create or update the Backlog Synthesizer dashboard in
# the Azure Portal via the Azure CLI.
#
# Prerequisites:
#   - Azure CLI installed:  brew install azure-cli
#   - Logged in to Azure:   az login
#   - Subscription set:     az account set --subscription <id>
#   - The infrastructure (Container App and Log Analytics) must be deployed.
#
# Usage:
#   chmod +x scripts/setup_azure_dashboard.sh
#   ./scripts/setup_azure_dashboard.sh
# =============================================================================

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
RESOURCE_GROUP="rg-backlog-synthesizer"
CONTAINERAPP_NAME="backlog-synthesizer"
LOCATION="eastus"
DASHBOARD_NAME="db-backlog-synthesizer-manual"

# Colours
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

# ── 1. Check Azure Login & Subscription ──────────────────────────────────────
SUBSCRIPTION=$(az account show --query id -o tsv 2>/dev/null || echo "")
if [ -z "$SUBSCRIPTION" ]; then
  warn "Not logged in to Azure. Please run 'az login' first."
  exit 1
fi
info "Using Subscription ID: $SUBSCRIPTION"

# ── 2. Check Resource Group ───────────────────────────────────────────────────
if ! az group show --name "$RESOURCE_GROUP" &>/dev/null; then
  warn "Resource group '$RESOURCE_GROUP' does not exist."
  warn "Please deploy the application first using scripts/azure_setup.sh or Terraform."
  exit 1
fi

# ── 3. Resolve Workspace & App details ────────────────────────────────────────
info "Resolving Container App details..."
APP_URL=$(az containerapp show \
  --name "$CONTAINERAPP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" \
  -o tsv 2>/dev/null || echo "")

if [ -z "$APP_URL" ]; then
  warn "Container App '$CONTAINERAPP_NAME' not found in Resource Group '$RESOURCE_GROUP'."
  exit 1
fi
APP_URL_FULL="https://${APP_URL}"
info "Container App URL: $APP_URL_FULL"

info "Resolving Log Analytics Workspace details..."
CAE_NAME=$(az containerapp show \
  --name "$CONTAINERAPP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "properties.managedEnvironmentId" -o tsv | xargs basename)

# Find the log analytics workspace from environment
WORKSPACE_RES_ID=$(az containerapp env show \
  --name "$CAE_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "properties.appLogsConfiguration.logAnalyticsConfiguration.customerId" \
  -o tsv 2>/dev/null || echo "")

# Look up workspace name using resource group list
WORKSPACE_NAME=$(az monitor log-analytics workspace list \
  --resource-group "$RESOURCE_GROUP" \
  --query "[0].name" -o tsv 2>/dev/null || echo "")

if [ -z "$WORKSPACE_NAME" ]; then
  # Fallback standard name if not found
  WORKSPACE_NAME="law-backlog-synthesizer"
fi

# Resolve the full resource ID of Log Analytics Workspace for Dashboard ComponentId
WORKSPACE_RES_ID="/subscriptions/${SUBSCRIPTION}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.OperationalInsights/workspaces/${WORKSPACE_NAME}"
info "Log Analytics Workspace Resource ID: $WORKSPACE_RES_ID"
info "Log Analytics Workspace Name: $WORKSPACE_NAME"

# ── 4. Generate Dashboard JSON from Template ─────────────────────────────────
info "Generating Dashboard JSON..."
TEMPLATE_PATH="infra/terraform/templates/dashboard.json.tpl"
OUTPUT_PATH="scripts/dashboard.json"

if [ ! -f "$TEMPLATE_PATH" ]; then
  warn "Template file not found at $TEMPLATE_PATH"
  exit 1
fi

# Use Python to substitute template variables cleanly
python3 -c "
import os
with open('$TEMPLATE_PATH', 'r') as f:
    content = f.read()

content = content.replace('\${workspace_id}', '$WORKSPACE_RES_ID')
content = content.replace('\${workspace_name}', '$WORKSPACE_NAME')
content = content.replace('\${subscription_id}', '$SUBSCRIPTION')
content = content.replace('\${resource_group_name}', '$RESOURCE_GROUP')
content = content.replace('\${app_url}', '$APP_URL_FULL')

with open('$OUTPUT_PATH', 'w') as f:
    f.write(content)
"

ok "Dashboard JSON compiled at: $OUTPUT_PATH"

# ── 5. Install Azure CLI Portal Extension if needed ──────────────────────────
# Check if portal extension is installed, if not az portal dashboard create will auto-install,
# but we can run it directly to ensure a smooth flow.
info "Ensuring az portal extension is installed..."
az extension add --name portal --yes --output none 2>/dev/null || true

# ── 6. Create Portal Dashboard ────────────────────────────────────────────────
info "Deploying Portal Dashboard '$DASHBOARD_NAME' to Resource Group '$RESOURCE_GROUP'..."

# Deploy
az portal dashboard create \
  --name "$DASHBOARD_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --input-path "$OUTPUT_PATH" \
  --output none

ok "Azure Monitor Dashboard deployed successfully!"
info "You can view the dashboard in the Azure Portal at:"
info "https://portal.azure.com/#blade/Microsoft_Azure_Monitoring/AzureMonitoringSharedDashboardBlade/dashboardId/%2Fsubscriptions%2F${SUBSCRIPTION}%2FresourceGroups%2F${RESOURCE_GROUP}%2Fproviders%2FMicrosoft.Portal%2Fdashboards%2F${DASHBOARD_NAME}"

# Cleanup
rm -f "$OUTPUT_PATH"
info "Temporary files cleaned up."
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Dashboard Setup Complete!                                 ${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo ""
