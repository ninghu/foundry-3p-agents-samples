# Hosting Microsoft Agent Framework agents on Azure Container Apps and surfacing them in Foundry

## Why this pairing matters

Foundry Agent Control Plane (FCP) offers orchestration, sharing, and observability for agents that follow its telemetry and interface expectations. Azure Container Apps (ACA) provides a serverless compute plane with integrated identity and scaling. Combining the two lets teams:

- Run Microsoft Agent Framework (MAF) agents in a managed container environment that can auto-scale and integrate with Azure networking.
- Emit OpenTelemetry traces and metrics that the FCP UI already understands.
- Expose the agent behind an authenticated A2A surface so it can participate in Foundry’s orchestration graph alongside other first- and third-party agents.

The repo’s `azure` sample mirrors the AWS and GCP counterparts while leaning into Azure-native capabilities. This post walks through the architecture, code, deployment workflow, and linkage points to Foundry.

## Architecture at a glance

| Layer | Component | Purpose |
| --- | --- | --- |
| Container hosting | Azure Container Apps | Runs a single-replica FastAPI service with KEDA-driven scale; uses managed identity for Azure resources. |
| Image supply | Azure Container Registry | Holds the agent image that `azd` builds and publishes. |
| Intelligence | Microsoft Agent Framework | Creates per-request conversation threads and binds a currency exchange tool. |
| Telemetry | Log Analytics + Application Insights | Receives OTEL spans exported via `azure.monitor.opentelemetry.exporter`. |
| Control plane | Foundry Agent Control Plane | Consumes OTEL data, exercises the A2A endpoint, and orchestrates flows. |

The heart of the service is `azure/agent_framework_container_app.py`. At startup the runtime either spins up a Microsoft Agent Framework chat agent (using `AzureAIAgentClient`) or enters a fallback mode when Azure AI credentials are missing. Each `/invoke` call:

1. Creates a fresh conversation thread to keep requests stateless.
2. Runs the user prompt through the agent.
3. Leverages `get_exchange_rate`—a Frankfurter API tool function—when the prompt calls for currency lookups.
4. Emits spans such as `agent.run` so downstream systems (e.g., FCP) can show conversation timelines.

The container listens on port 80. Ingress is public for demo purposes but can be fronted by ACA authentication providers or APIM.

## Deployment workflow with `azd`

The new `azure.yaml` and `infra/` assets turn the sample into an Azure Developer CLI (`azd`) template:

- `infra/main.bicep` creates Log Analytics, Application Insights, Container Registry, and Container App resources, wiring managed identity and OTEL settings.
- `azure.yaml` defines the `agentapp` service, telling `azd` to build from `azure/Dockerfile`, push into the provisioned registry, and apply Bicep parameters.
- `env/.env.sample` documents the required secrets: `AZURE_AI_PROJECT_ENDPOINT`, `AZURE_AI_MODEL_DEPLOYMENT_NAME` or `AZURE_AI_AGENT_ID`, plus optional overrides.

End-to-end provisioning and deployment boils down to:

```powershell
azd env new <env-name> --location <azure-region>
azd up
```

The `azd up` pipeline packages the container, deploys infra, assigns AcrPull permissions, and surfaces outputs such as `service__agentapp__host`. After the run, health can be verified with:

```powershell
Invoke-WebRequest -Uri https://<fqdn>/healthz
Invoke-RestMethod -Uri https://<fqdn>/invoke -Method Post -Body (@{ prompt = "How do I convert 100 USD to EUR?" } | ConvertTo-Json) -ContentType "application/json"
```

By default the code falls back to an instructional message until Azure AI Foundry credentials are provided. Supplying the environment variables and rerunning `azd up` enables the full Microsoft Agent Framework flow.

## OpenTelemetry and Foundry expectations

MAF integrates tightly with OTEL. In `_configure_tracer`, the FastAPI app creates a `TracerProvider` that exports to Azure Monitor. Those spans land in Application Insights and Log Analytics, which can be bridged to Foundry’s observability plane. The exported attributes include:

- `service.name`: aligns with the SKU label shown in Foundry dashboards.
- `service.namespace`: set to `foundry-3p-agents` for easy filtering.
- `service.instance.id`: captures the container host.

On the Foundry side:

1. Register an external ACA endpoint as an A2A agent, pointing at `https://<fqdn>/invoke`.
2. Configure authentication—Managed Identity or Foundry-issued tokens—depending on your security stance.
3. Map OTEL ingestion (Application Insights or forwarding pipeline) so traces and metrics show in the FCP UI.

Once registered, the ACA-hosted agent becomes available to orchestration recipes—Foundry can invoke it, inspect latency, and chain it with other tools.

## Hardening considerations

- **Identity**: Switch to ACA’s workload identity for token exchange and scope its permissions to only what the agent needs (e.g., specific Azure AI project).
- **Secrets**: Store connection strings in Key Vault or use ACA secrets rather than plaintext environment variables in `.env`.
- **Networking**: Bring the Container App into a virtual network and expose the agent over Private Link for enterprise scenarios.
- **Scaling**: Adjust `scale.minReplicas` and `scale.maxReplicas` along with custom KEDA rules if you expect bursty traffic.

## Where to extend next

- Add more tools (e.g., market data APIs) through Microsoft Agent Framework’s tool binding system.
- Layer Azure Front Door or API Management in front of the Container App for auth, throttling, and observability integration.
- Instrument business metrics alongside OTEL traces to power FCP dashboards.
- Package the sample as an internal accelerator so teams can scaffold new agents with a single `azd init`.

With the infrastructure, code, and deployment pipeline in place, the remaining work pivots to wiring the Foundry registration and documenting operational runbooks—see `aca-blog-next-steps.md` once drafted.
