# Foundry Third-Party Agents Samples

Minimal samples showing how to instrument third‑party agent frameworks for observability with Azure Application Insights (via OpenTelemetry).

## Overview

- Focus: high‑signal, compact examples of tracing agent runs and tool calls.
- Providers covered: AWS Bedrock, Google Vertex AI.
- Each folder has its own short README with setup and run steps.

## Folders

- `aws/agent_core/` - Bedrock + LangGraph + AgentCore sample with Azure tracing. See `aws/agent_core/README.md`.
- `gcp/vertax/` - Vertex AI LangChain agent sample with Azure tracing. See `gcp/vertax/README.md`.

## Getting Started

- Pick a provider folder and follow its README.
- Requirements are listed per folder (e.g., `aws/agent_core/requirements.txt`).

For sample HTTP endpoints and payloads, see the provider READMEs.
