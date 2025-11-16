# Foundry Third-Party Agents Samples

Minimal samples showing how to instrument third‑party agent frameworks for observability with Azure Application Insights (via OpenTelemetry).

## Overview

- Focus: high‑signal, compact examples of tracing agent runs and tool calls.
- Providers covered: AWS Bedrock, Google Vertex AI.
- Each folder has its own short README with setup and run steps.

## Folders

- `aws/` — Bedrock + LangGraph + AgentCore sample with Azure tracing. See `aws/README.md`.
- `gcp/` — Vertex AI LangChain agent samples with Azure tracing. See `gcp/README.md`.
- `azure/` — Microsoft Agent Framework sample for Azure Container Apps with Azure Monitor tracing. See `azure/README.md`.

## Getting Started

- Pick a provider folder and follow its README.
- Requirements are listed per folder (e.g., `requirements.txt`).
- To deploy the Azure sample end-to-end with Azure Developer CLI, run `azd up` from the repo root after configuring `env/.env.sample`.

## HTTP Endpoints

### Vertex AI

POST `https://us-west1-aiplatform.googleapis.com/v1/projects/ninhu-project1/locations/us-west1/reasoningEngines/1304319857705091072:query`

Body:

```json
{
  "input": { "input": "What is the exchange rate from US dollars to SEK today?" }
}
```

Headers:

```http
Authorization: Bearer <token>
```

Get token:

```bash
gcloud auth application-default print-access-token
```

### AWS AgentCore

POST `https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-west-2%3A025211824558%3Aruntime%2Fagentcore_langgraph_agent-1EC4Au3NoU/invocations?qualifier=DEFAULT`

Body:

```json
{
  "prompt": "What is the exchange rate from USD to EUR today?"
}
```

Auth: AWS Signature Version 4 (SigV4)
