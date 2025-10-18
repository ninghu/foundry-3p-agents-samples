# AWS Bedrock + LangGraph (AgentCore)

Simple currency-exchange agent running on AWS Bedrock via LangGraph/AgentCore, with traces sent to Azure Application Insights.

## Quick Start

- Install deps (from repo root): `pip install -r aws/agent_core/requirements.txt`
- Configure AWS auth (e.g., environment/CLI) and update constants in `aws/agent_core/agentcore_langgraph_agent.py` (`AWS_REGION`, `BEDROCK_MODEL_ID`, `APPLICATION_INSIGHTS_CONNECTION_STRING`, `AGENT_NAME`, `AGENT_ID`).
- Run locally: `python aws/agent_core/agentcore_langgraph_agent.py`
- Optional Docker: `docker build -t agentcore-aws aws/agent_core && docker run -p 8080:8080 agentcore-aws`

## HTTP Endpoint

POST `https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-west-2%3A025211824558%3Aruntime%2Fagentcore_langgraph_agent-1EC4Au3NoU/invocations?qualifier=DEFAULT`

Body:

```
{
  "prompt": "What is the exchange rate from USD to EUR today?"
}
```

Auth: AWS Signature Version 4 (SigV4)

## Files

- `agentcore_langgraph_agent.py` - main app; emits traces via `AzureAIOpenTelemetryTracer`.
- `.bedrock_agentcore.yaml` - AgentCore runtime/deployment config (update IDs/ARNs to match your AWS account).
- `Dockerfile`, `requirements.txt` - container and Python deps.

