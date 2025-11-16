# Follow-up checklist for Foundry integration

## 1. Harden the Azure deployment

- [ ] Switch Application Insights connection string and Azure AI credentials to ACA secrets or Key Vault references.
- [ ] Enable ACA-managed identity permissions against the Azure AI project (or bring-your-own service principal) with least privilege.
- [ ] Decide whether the Container App should sit behind an internal ingress, APIM, or Front Door for enterprise access.
- [ ] Update `infra/main.bicep` if private networking, custom domains, or TLS certs are required.

## 2. Configure Microsoft Agent Framework access

- [ ] Provision or import an Azure AI Foundry project endpoint.
- [ ] Either **set `AZURE_AI_MODEL_DEPLOYMENT_NAME`** (for ephemeral agents) or **set `AZURE_AI_AGENT_ID`** (for pre-built agents) in the azd environment.
- [ ] Run `azd up` to redeploy with the credentials and confirm `/invoke` returns model-backed responses.

## 3. Register the agent in Foundry Control Plane (FCP)

- [ ] Create an A2A agent entry pointing at `https://<aca-fqdn>/invoke`.
- [ ] Choose authentication (Managed Identity, Foundry-issued token, API key) and configure ACA accordingly.
- [ ] Define the contract: schema for request/response payloads, tool metadata, and usage policies.
- [ ] Validate the agent appears in Foundry’s inventory and can be invoked from the console.

## 4. Wire telemetry into Foundry

- [ ] Confirm OTEL traces from Application Insights (or the Log Analytics workspace) are flowing into FCP’s telemetry pipeline.
- [ ] Map service name/namespace filters so the FCP UI can display latency, tool calls, and errors.
- [ ] Optionally emit custom spans or metrics (e.g., tool success rate) for richer dashboards.

## 5. Enable orchestration scenarios

- [ ] Add the ACA agent to existing Foundry flows or playbooks.
- [ ] Define routing rules or guardrails if other agents should call this one conditionally.
- [ ] Document failure-handling: retries, fallbacks, or manual escalation paths.

## 6. Documentation & support

- [ ] Extend `ACA-blog.md` with final screenshots, logs, and Foundry UI captures.
- [ ] Produce an operations runbook covering scaling, updates, and incident response.
- [ ] Share the template and blog with partner teams—collect feedback for improvements.
