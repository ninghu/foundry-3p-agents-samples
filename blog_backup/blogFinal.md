# Integrate Azure Container Apps Agents with Azure AI Foundry for Orchestration

## 1. Introduction

Agentic applications are no longer single, monolithic bots. Teams now stitch together specialized agents—pricing, search, fulfillment—that need to scale independently, ship fast, and still look like a single brain to the end user. This post walks through one concrete blueprint: build a currency-exchange agent with the Microsoft Agent Framework, host it on Azure Container Apps (ACA), wire in OpenTelemetry-based observability, and expose it to Azure AI Foundry orchestrators through Agent-to-Agent (A2A) connections. By the end you can deploy, observe, and hand off to the ACA agent entirely through code while keeping the Foundry UI as your command center.

> ✅ Deliverables: ACA-hosted FastAPI service, azd-driven infrastructure deployment, Application Insights traces, and an A2A-registered tool callable from Foundry.

## 2. Why Azure Container Apps is a Great Runtime for Agents

- **Serverless containers** – You package the agent once; ACA handles the infrastructure, certificates, and base scaling rules.
- **Autoscaling knobs** – Scale on HTTP concurrency, events, or custom KEDA metrics so bursts of agent traffic don’t drown other workloads.
- **Cost-optimized microservices** – Pay per vCPU-second and memory-second; idle pods can scale to zero.
- **Multi-agent friendly** – Each agent (or skill) can live in its own Container App inside the same managed environment, simplifying network policy and secret distribution.
- **Portability** – Images are standard OCI artifacts. If you later need AKS or another cloud, you already have the container.
- **Batteries included** – Managed identities, secrets, Dapr, and VNET integration are available without extra plumbing.

## 3. Why Azure AI Foundry for Orchestration

- **Multi-step coordination** – Foundry agents fan out work to other agents or tools, making it the natural conductor for multi-agent systems.
- **Agent-to-Agent (A2A) connections** – Securely call remote agents (ACA, Functions, App Service, on-prem) as first-class tools.
- **Tooling ecosystem** – Built-in evaluators, tracing, red-teaming, and test harnesses shorten the production hardening cycle.
- **Observability** – Foundry Monitoring surfaces every tool call, prompt, and completion so you always know when your ACA agent was invoked.
- **Governance** – Centralized projects, permissions, and secrets keep compliance teams happy while you experiment.

## 4. Architecture Overview

```text
User / App → Foundry Orchestrator Agent → (A2A HTTPS) → ACA-hosted FastAPI Agent
                         ↓
                     Azure Monitor (Application Insights + Log Analytics)
                   Container Registry + Managed Environment + Managed Identity
```

1. A Foundry orchestrator receives a user message and decides it needs exchange-rate info.
2. The orchestrator issues an A2A tool call that targets the ACA agent’s `/invoke` endpoint.
3. ACA runs the FastAPI + Microsoft Agent Framework service, uses Azure AI Foundry (or Azure OpenAI) for LLM completions, and returns the answer.
4. OpenTelemetry spans stream into Application Insights, and Foundry Monitoring captures the full tool-call lineage.

## 5. Build an ACA Agent Using the Microsoft Agent Framework

Create a FastAPI entry point that boots the Microsoft Agent Framework (MAF) runtime and exposes both standard HTTP routes and the optional A2A surface:

```python
app = FastAPI(title="Azure Container Apps - Microsoft Agent Framework Sample", version="0.1.0")

runtime = AgentRuntime()

@app.on_event("startup")
async def on_startup() -> None:
    await runtime.startup()

@app.post("/invoke")
async def invoke(request: PromptRequest) -> Dict[str, str]:
    answer = await runtime.run(request.prompt)
    return {"result": answer}
```

Key MAF components in `azure/agent_framework_container_app.py`:

- `AgentRuntime.startup()` loads credentials via `DefaultAzureCredential`, then attaches to either an existing Azure AI agent (`AZURE_AI_AGENT_ID`) or creates an ephemeral one using `AZURE_AI_MODEL_DEPLOYMENT_NAME`.
- `get_exchange_rate()` is registered as a tool so the agent can reach out to the Frankfurter API when reasoning about currency.
- `_build_a2a_application()` mounts the Starlette-powered A2A routes under `/a2a`, publishing an agent card and RPC endpoint compatible with Foundry.

## 6. Add OpenTelemetry Instrumentation

Observability is enabled at import-time:

```python
def _configure_observability() -> None:
    configure_azure_monitor(
        resource=Resource.create({"service.name": SERVICE_NAME}),
        connection_string=APPLICATION_INSIGHTS_CONNECTION_STRING,
    )
    setup_observability(enable_sensitive_data=False)
```

Highlights:

- `configure_azure_monitor` wires the OpenTelemetry exporter to Application Insights using the connection string injected as a secret.
- `setup_observability()` (from MAF) emits `gen_ai.*` spans for every agent run, making it easy to correlate LLM usage with Foundry tool calls.
- The FastAPI app also logs HTTP request spans so ACA ingress metrics and Foundry Monitoring show matching timelines.

## 7. Send Traces to Application Insights

`infra/main.bicep` provisions both Log Analytics and Application Insights. The container’s secrets block injects the connection string:

```bicep
secrets: [
  {
    name: 'application-insights-connection-string'
    value: appInsights.properties.ConnectionString
  }
]
```

The container’s environment variables then reference the secret:

```bicep
{
  name: 'APPLICATION_INSIGHTS_CONNECTION_STRING'
  secretRef: 'application-insights-connection-string'
}
```

After deployment:

- **Check traces:** Open Application Insights → Logs and run a Kusto query such as:

  ```kusto
  traces
  | where customDimensions['gen_ai.operation.name'] == 'agent.run'
  | top 20 by timestamp desc
  ```

- **Validate span names:** Expect entries like `gen_ai.agent.run`, `HTTP POST /invoke`, and `tool.call.exchange_rate`.
- **Optional local testing:** Point OTLP to a collector by exporting `OTEL_EXPORTER_OTLP_ENDPOINT` if you want to test without Azure Monitor.

## 8. Deploy to Azure Container Apps with azd

The `azure/azure.yaml` manifest plus `infra/main.bicep` allow one-command provisioning:

```powershell
cd azure
azd env new agent-sample
Copy-Item env/.env.sample .azure/agent-sample/.env  # update location, subscription, feature flags
azd up
```

`azd up` performs the following:

- Builds and pushes the container to the generated Azure Container Registry.
- Provisions Log Analytics, App Insights, Container Apps environment, Container App, and (optionally) Azure AI hub/project and Azure OpenAI resources based on `.env` toggles (`ENABLE_AZURE_AI_PROJECT`, `ENABLE_AZURE_OPENAI`).
- Assigns the Container App’s managed identity the AcrPull role and injects all secrets/environment variables listed in the `.env` file.
- Outputs useful values such as `SERVICE_AGENTAPP_HOST` (the public FQDN) so you can smoke-test with:

  ```powershell
  $env:ACA_ENDPOINT = azd env get-value SERVICE_AGENTAPP_HOST
  Invoke-RestMethod -Method Post "https://$env:ACA_ENDPOINT/invoke" -Body (@{ prompt = "Convert 100 USD to SEK" } | ConvertTo-Json) -ContentType "application/json"
  ```

## 9. Connect the ACA Agent to Azure AI Foundry via A2A

1. **Create the A2A connection** – In Foundry Studio, go to **Connected resources → Connections → Add connection → Agent-to-Agent (A2A)**. Supply the ACA HTTPS endpoint (e.g., `https://<SERVICE_AGENTAPP_HOST>/a2a`) and choose an auth method (Managed Identity or key).
2. **Register the tool in code** – A2A tools only exist in code/REST. Using the Azure AI Projects SDK:

   ```python
   agent = project.agents.create_agent(
     model="gpt-4o-mini",
     name="orchestrator",
     tools=[
       {
         "type": "agent",
         "agent_id": "aca-currency-agent",
         "connection": "aca-currency-connection"
       }
     ]
   )
   ```

3. **Test the hand-off** – Create a thread, drop a user message, and start a run. In Monitoring → Tracing you should see a tool call such as `POST https://<SERVICE_AGENTAPP_HOST>/a2a/rpc`.

## 10. Validate End-to-End Observability

- **Azure Container Apps** – Use `az containerapp logs show --name <app> --resource-group <rg>` for live logs or attach Log Analytics queries to the ACA environment workspace.
- **Application Insights** – Query for `gen_ai.*` spans, HTTP requests, and dependency calls to the Frankfurter API. Inspect correlation IDs to trace a user request through the entire pipeline.
- **Azure AI Foundry Monitoring** – Confirm that the orchestrator recorded the tool call, latency, and token usage. Failed calls surface here with HTTP status codes, making it easy to detect ACA misconfigurations.
- **Evaluator harness (optional)** – After `azd env refresh`, copy the exported Azure AI and Azure OpenAI values into `evals/a2a/.env`, then run the provided evaluator scripts to capture success metrics.

## 11. Conclusion & Next Steps

By combining ACA, Microsoft Agent Framework, Azure Monitor, and Foundry’s A2A pipeline, you get a repeatable pattern for production-grade agents:

- **ACA + MAF** keeps each agent isolated, autoscalable, and easy to iterate on.
- **Foundry** provides orchestration, governance, and evaluation across all those agents.
- **Application Insights** gives deep visibility into every prompt, tool invocation, and external dependency.

From here you can:

- Enable GPUs or custom scale rules if your agent needs heavier models.
- Flip on `ENABLE_AZURE_AI_PROJECT` to let `azd` create a dedicated AI hub/project per environment.
- Add CI/CD (GitHub Actions, Azure Pipelines) that runs `azd up` on merge.
- Layer in managed identities per agent or private VNET ingress for stricter security.
- Extend the evaluator suite in `evals/a2a` to cover regression tests before promoting to production.

Happy building—see you in Monitoring → Tracing when your orchestrator calls into ACA! 🚀




#
At Microsoft Ignite we’re spotlighting how agents built on Azure Container Apps with the Microsoft Agent Framework plug directly into Azure AI Foundry, giving teams a single pane of glass for orchestration, governance, and rapid iteration.
#

Azure Container Apps (ACA) is a fully managed serverless container platform that enables developers to design and deploy microservices and modern apps without requiring container expertise or needing infrastructure management.

ACA is rapidly emerging as the preferred platform for hosting AI workloads and intelligent agents in the cloud. With features like code interpreter, Serverless GPUs, simplified deployments, and per-second billing, ACA empowers developers to build, deploy, and scale AI-driven applications with exceptional agility. ACA makes it easy to integrate agent frameworks, leverage GPU acceleration, and manage complex, multi-container AI environments - all while benefiting from a serverless, fully managed infrastructure. External customers like Replit, NFL Combine, Coca-Cola, and European Space Agency as well as internal teams like Microsoft Copilot (as well as many others) have bet on ACA as their compute platform for AI workloads.

ACA is quickly becoming the leading platform for updating existing applications and moving them to a cloud-native setup. It allows organizations to seamlessly migrate legacy workloads - such as Java and .NET apps - by using AI-powered tools like GitHub Copilot to automate code upgrades, analyze dependencies, and handle cloud transformations. ACA’s fully managed, serverless environment removes the complexity of container orchestration. This helps teams break down monolithic or on-premises applications into robust microservices, making use of features like version control, traffic management, and advanced networking for fast iteration and deployment. By following proven modernization strategies while ensuring strong security, scalability, and developer efficiency, ACA helps organizations continuously innovate and future-proof their applications in the cloud. Customers like EY, London Stock Exchange, Chevron, and Paychex have unlocked significant business value by modernizing their workloads onto ACA. 

This blog presents the latest features and capabilities of ACA, enhancing its value for customers by enabling the rapid migration of existing workloads and development of new cloud applications, all while following cloud-native best practices.