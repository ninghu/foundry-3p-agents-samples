# AWS Bedrock + LangGraph (AgentCore)

Simple currency‑exchange agent running on AWS Bedrock via LangGraph/AgentCore, with traces sent to Azure Application Insights.

## Quick Start

- Install deps: `pip install -r aws/requirements.txt`
- Configure AWS auth (e.g., environment/CLI) and update constants in `aws/agentcore_langgraph_agent.py` (`AWS_REGION`, `BEDROCK_MODEL_ID`, `APPLICATION_INSIGHTS_CONNECTION_STRING`).
- Run locally: `python aws/agentcore_langgraph_agent.py`
- Optional Docker: `docker build -t agentcore-aws aws && docker run -p 8080:8080 agentcore-aws`

## Files

- `agentcore_langgraph_agent.py` — main app; Azure tracer via `AzureAIOpenTelemetryTracer`.
- `.bedrock_agentcore.yaml` — AgentCore runtime/deployment config.
- `Dockerfile`, `requirements.txt` — container and Python deps.

