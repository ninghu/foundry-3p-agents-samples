# Azure Container Apps + Microsoft Agent Framework

Currency exchange agent sample running inside Azure Container Apps (ACA) using the Microsoft Agent Framework, with traces sent to Azure Application Insights through OpenTelemetry.

## Quick Start

1. **Install dependencies**

   ```powershell
   pip install -r azure/requirements.txt
   ```

2. **Authenticate with Azure**

   ```powershell
   az login
   ```

3. **Export required environment variables**

   ```powershell
   $env:AZURE_AI_PROJECT_ENDPOINT="https://<your-project>.services.ai.azure.com/api/projects/<project-id>"
   $env:AZURE_AI_MODEL_DEPLOYMENT_NAME="gpt-4o-mini"
   $env:APPLICATION_INSIGHTS_CONNECTION_STRING="InstrumentationKey=..."
   # Optional when using an existing agent
   # $env:AZURE_AI_AGENT_ID="<existing-agent-id>"
   # Optional: provide Azure OpenAI credentials if running evaluators locally
   # $env:AZURE_OPENAI_ENDPOINT="https://<your-azure-openai>.openai.azure.com/"
   # $env:AZURE_OPENAI_DEPLOYMENT="gpt-4o-mini"
   # $env:AZURE_OPENAI_KEY="<azure-openai-key>"
   ```

4. **Run locally**

   ```powershell
   uvicorn azure.agent_framework_container_app:app --host 0.0.0.0 --port 8080 --reload
   ```

5. **Query the agent**

   ```powershell
   Invoke-RestMethod -Method Post -Uri "http://localhost:8080/invoke" -Body (@{ prompt = "How much is 100 USD in SEK today?" } | ConvertTo-Json) -ContentType "application/json"
   ```

## Deploy with Azure Developer CLI (`azd`)

1. **Create an environment** *(run from the `azure/` folder)*

   ```powershell
   Push-Location azure
   azd env new agent-sample
   Copy-Item env/.env.sample .azure/agent-sample/.env
   notepad .azure/agent-sample/.env  # Update subscription, location, and provisioning toggles
   ```

   The `.env.sample` now includes switches to provision an Azure AI hub/project and an Azure OpenAI resource. Leave `AZURE_AI_PROJECT_ENDPOINT` blank and keep `ENABLE_AZURE_AI_PROJECT=true` to let `azd up` create a fresh Azure AI Foundry project that shares the same Application Insights instance as the Container App. Set `ENABLE_AZURE_OPENAI=true` (default) to create an Azure OpenAI account + GPT-4o mini deployment for the evaluator scripts. If you prefer to reuse existing resources, flip either toggle to `false` and supply the corresponding endpoint values manually.

2. **Provision and deploy**

   ```powershell
   azd up
   ```

3. **Test the deployed endpoint**

   ```powershell
   $env:ACA_ENDPOINT = azd env get-value SERVICE_AGENTAPP_HOST
   Invoke-RestMethod -Method Post -Uri "https://$env:ACA_ENDPOINT/invoke" -Body (@{ prompt = "Current USD to SEK rate?" } | ConvertTo-Json) -ContentType "application/json"
   Pop-Location
   ```

4. **Sync outputs for evaluation tooling (optional)**

   After `azd up`, pull the generated Azure AI / Azure OpenAI endpoints into your environment file:

   ```powershell
   Push-Location azure
   azd env refresh
   azd env get-values | ConvertFrom-Json | Select-Object AZURE_AI_PROJECT_ENDPOINT,AZURE_OPENAI_ENDPOINT,AZURE_OPENAI_DEPLOYMENT
   Pop-Location
   ```

   The Azure OpenAI API key is stored with the Container App as the secret `azure-openai-key`. You can retrieve it with:

   ```powershell
    az cognitiveservices account keys list --ids (azd env get-value AZURE_OPENAI_RESOURCE_ID)
   ```

   Copy those values into `evals/a2a/.env` before running the evaluator harness.

`azd up` provisions the infrastructure defined in `infra/main.bicep` (Container Apps environment, Container Registry, Log Analytics, Application Insights) and deploys the container using managed identity. Update the generated `.env` file with your Azure AI project endpoint, model deployment name, and optional agent ID before running the command.

## Azure Container Apps deployment (sample)

```powershell
$ACA_RG="rg-agents"
$ACA_ENV="agents-env"
$ACA_NAME="aca-agent-framework"

az group create --name $ACA_RG --location westus3
az containerapp env create --name $ACA_ENV --resource-group $ACA_RG --location westus3

az containerapp up `
  --name $ACA_NAME `
  --resource-group $ACA_RG `
  --environment $ACA_ENV `
  --source ./azure `
  --target-port 8080 `
  --ingress external `
  --env-vars "AZURE_AI_PROJECT_ENDPOINT=$env:AZURE_AI_PROJECT_ENDPOINT" "AZURE_AI_MODEL_DEPLOYMENT_NAME=$env:AZURE_AI_MODEL_DEPLOYMENT_NAME" "APPLICATION_INSIGHTS_CONNECTION_STRING=$env:APPLICATION_INSIGHTS_CONNECTION_STRING"
```

> When running inside ACA, prefer **Managed Identity** for authentication. After assigning a user-assigned or system-assigned identity to the container app, remove local credentials and let `DefaultAzureCredential` pick up the managed identity automatically.

## Files

- `agent_framework_container_app.py` – FastAPI host that wires Microsoft Agent Framework to ACA and emits Application Insights traces.
- `Dockerfile` – Container image definition for local or ACA deployment.
- `requirements.txt` – Python dependencies.
- `azure.yaml` – Azure Developer CLI manifest that now lives alongside the app code.
- `infra/` – Bicep template (`main.bicep`), compiled ARM template, and parameters used by `azd`.
- `env/` – Sample `.env` file seeded when you create a new azd environment.

## Observability

- Traces are exported through `opentelemetry-exporter-azuremonitor` when `APPLICATION_INSIGHTS_CONNECTION_STRING` is set.
- The sample produces spans per agent invocation (`agent.run`) and exposes a `/healthz` endpoint for container probes.

## References

- [Microsoft Agent Framework Azure AI agents (Python)](https://learn.microsoft.com/en-us/agent-framework/user-guide/agents/agent-types/azure-ai-foundry-agent?pivots=programming-language-python)
- [Hosting agents in Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/)
- [Azure Monitor OpenTelemetry exporter](https://learn.microsoft.com/en-us/azure/azure-monitor/app/opentelemetry-enable?tabs=python)
