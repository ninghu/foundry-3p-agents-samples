# Foundry Third-Party Agents Samples

Reference implementations that show how to plug Azure Application Insights into agents running on AWS Bedrock, Google Cloud, and the A2A protocol. Every sample has an end-to-end README plus an `.env.example` you can copy to start experimenting locally.

## What’s inside
- `aws/agent_core/` – LangGraph + AgentCore currency agent that calls Bedrock models and, when available, streams traces to Azure Application Insights.
- `gcp/cloud_run_agent/` – FastAPI service that hosts a multi-stage LangGraph travel planner on Cloud Run with Gemini, optional Azure tracing, and a helper `deploy.py`.
- `gcp/vertex/` – Vertex AI Agent Engines sample that builds a LangChain `LangchainAgent`, wires in a currency-rate tool, and forwards spans to Azure when configured.
- `gcp/a2a_agent/` – Remote LangGraph currency agent packaged with the A2A server runtime, Dockerfile, deployment helper, and its own README.
- `evals/` – Evaluation utilities, including `trace/trace_eval.py` for Application Insights traces and `a2a/a2a_agent_eval.py` for driving the remote agent over A2A connections.

## How to use the repo
1. Pick the scenario you care about and open its README for architecture, setup, and deployment notes.
2. Copy the matching `.env.example` file, fill in your cloud credentials, and install the requirements listed for that sample.
3. Follow the per-sample README to run locally or deploy; when `APPLICATION_INSIGHTS_CONNECTION_STRING` (or equivalent) is set, telemetry flows into Azure automatically.
