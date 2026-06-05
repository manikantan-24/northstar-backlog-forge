# Deploy to Azure via GitHub Actions

This guide gets the Backlog Synthesizer running on **Azure Container Apps** with
a full CI/CD pipeline through GitHub Actions. Every push to `main` runs tests,
builds a Docker image, pushes to Azure Container Registry, and deploys
automatically — zero manual steps after the first setup.

---

## Architecture

```
GitHub push to main
    │
    ├─► GitHub Actions: Tests + Lint  (ci.yml)
    │
    └─► GitHub Actions: Build + Deploy  (deploy.yml)
            │
            ├─► Docker build
            ├─► Push to Azure Container Registry (ACR)
            └─► Deploy to Azure Container Apps
                    │
                    ├─► Secrets from Azure Key Vault
                    └─► Persistent storage via Azure Files
                          (logs/, outputs/)
```

**Cost estimate**: ~$6–10/month for a demo/submission workload.
Container Apps scales to zero when idle — no requests = $0 compute.

---

## Prerequisites

- Azure CLI: `brew install azure-cli` then `az login`
- Terraform ≥ 1.5: `brew install terraform`
- GitHub repo with Actions enabled

---

## Step 1 — Provision Azure infrastructure with Terraform

```bash
cd infra/terraform

# Copy and fill in your values
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars — at minimum set anthropic_api_key
# Change acr_name, keyvault_name, storage_account_name to globally unique values

# Initialise and plan
terraform init
terraform plan -out=tfplan

# Apply (creates all resources — takes ~3 minutes)
terraform apply tfplan
```

After apply, Terraform prints the values you need for GitHub secrets:

```bash
terraform output app_url                     # your live URL
terraform output acr_login_server            # → ACR_REGISTRY secret
terraform output resource_group_name         # → AZURE_RESOURCE_GROUP secret
terraform output container_app_name          # → CONTAINERAPP_NAME secret
terraform output -raw github_actions_credentials  # → AZURE_CREDENTIALS secret
```

---

## Step 2 — Add GitHub Secrets

Go to your repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value | Where to get it |
|---|---|---|
| `AZURE_CREDENTIALS` | JSON blob | `terraform output -raw github_actions_credentials` |
| `ACR_REGISTRY` | e.g. `backlogsynth.azurecr.io` | `terraform output acr_login_server` |
| `AZURE_RESOURCE_GROUP` | e.g. `rg-backlog-synthesizer` | `terraform output resource_group_name` |
| `CONTAINERAPP_NAME` | e.g. `backlog-synthesizer` | `terraform output container_app_name` |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Anthropic console |
| `GOOGLE_API_KEY` | optional | Google AI Studio |
| `JIRA_BASE_URL` | optional | your Atlassian tenant URL |
| `JIRA_EMAIL` | optional | your Atlassian email |
| `JIRA_API_TOKEN` | optional | Atlassian API tokens page |
| `JIRA_PROJECT_KEY` | optional | your Jira project key |

---

## Step 3 — Push to main

```bash
git add .
git commit -m "chore: configure Azure deployment"
git push origin main
```

GitHub Actions will:
1. Run tests (must pass)
2. Build Docker image
3. Push to ACR
4. Deploy to Container Apps
5. Run a health check
6. Print the live URL in the workflow summary

Watch it: `https://github.com/<your-org>/<repo>/actions`

---

## Step 4 — Verify deployment

Open the URL from the workflow summary (or run `terraform output app_url`).

You should see the Backlog Synthesizer login page. Log in with the credentials
in `config/auth.yaml`.

> **First login note**: `auth.yaml` is baked into the Docker image. To change
> passwords after deployment, update `config/auth.yaml` locally and push to
> main — the next deploy will pick them up.

---

## GitHub Actions workflows

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | Every push / PR | Tests, lint, Docker build check, (optional) eval suite |
| `deploy.yml` | Push to `main` | Tests → build → push ACR → deploy Container Apps → health check |

### Manual deploy to production

```
GitHub → Actions → Deploy to Azure Container Apps → Run workflow
Environment: production
Reason: Release v1.0
```

---

## Environment management

The deploy workflow supports two environments: `staging` (automatic, on every
push to main) and `production` (manual trigger only).

To add a production environment with required reviewers:
1. Go to repo **Settings → Environments → New environment → production**
2. Add required reviewers (people who must approve before prod deploy runs)
3. Trigger a production deploy via **Actions → Run workflow**

---

## Secrets rotation

To rotate the Anthropic API key:

```bash
# Update in Key Vault
az keyvault secret set \
  --vault-name kv-backlog-synth \
  --name ANTHROPIC-API-KEY \
  --value "sk-ant-new-key..."

# Update GitHub secret (so new containers get it too)
gh secret set ANTHROPIC_API_KEY --body "sk-ant-new-key..."

# Redeploy to pick up new value
gh workflow run deploy.yml
```

---

## Teardown

```bash
# Destroy all Azure resources
cd infra/terraform
terraform destroy

# Or just delete the resource group (faster, bypasses Terraform state)
az group delete --name rg-backlog-synthesizer --yes
```

---

## Troubleshooting

**Deployment failed — health check timed out**
- Check Container App logs: `az containerapp logs show --name backlog-synthesizer --resource-group rg-backlog-synthesizer --follow`
- Common cause: `ANTHROPIC_API_KEY` secret missing or wrong

**Login page shows "Auth not configured"**
- The `config/auth.yaml` was not copied into the Docker image
- Check `.dockerignore` — make sure `config/` is not excluded
- Rebuild: push a commit or trigger the workflow manually

**Image push to ACR fails**
- The service principal may have lost ACR push permissions
- Re-run: `terraform apply` (safe — only repairs drift)

**`az containerapp update` command not found in workflow**
- The runner may need the Container Apps CLI extension
- Add to workflow before the deploy step:
  `az extension add --name containerapp --upgrade`
