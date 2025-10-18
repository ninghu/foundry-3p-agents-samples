# GCP Vertex AI (LangChain)

Compact sample showing a LangChain agent on Vertex AI with traces sent to Azure Application Insights.

## Quick Start

- Install deps (from repo root): `pip install -r gcp/vertax/requirements.txt`
- Authenticate: `gcloud auth application-default login`
- Update constants in `gcp/vertax/vertex_langchain_agent.py` (`project`, `location`, `model_name`, `application_insights_connection_string`, `agent_name`, `agent_id`, `provider_name`).
- Run locally: `python gcp/vertax/vertex_langchain_agent.py`
- Optional deploy: uncomment `deploy_agent(...)` in the `__main__` block and ensure `staging_bucket` points to your GCS bucket.

## HTTP Endpoint

POST `https://us-west1-aiplatform.googleapis.com/v1/projects/ninhu-project1/locations/us-west1/reasoningEngines/1304319857705091072:query`

Body:

```
{
  "input": { "input": "What is the exchange rate from US dollars to SEK today?" }
}
```

Headers:

```
Authorization: Bearer <token>
```

Get token:

```
gcloud auth application-default print-access-token
```

## Notes

- Tracing uses `AzureAIOpenTelemetryTracer` in a custom runnable builder.
- Keep `enable_tracing=False` on the Vertex agent to avoid conflicts with the Azure tracer.
- Remote deployment packages the repo with `gcp/vertax/requirements.txt`; confirm dependencies are compatible with Vertex Agent Engines.
