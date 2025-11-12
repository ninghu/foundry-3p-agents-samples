# Vertex AI LangChain Agent

This sample builds a LangChain `LangchainAgent` with a currency-exchange tool, runs it locally, and (optionally) deploys it to Vertex AI Agent Engines. Azure Application Insights spans are emitted automatically when the optional `langchain-azure-ai` dependency and connection string are present.

## Prerequisites
- Python 3.12+
- Google Cloud project with Vertex AI Agent Engines enabled
- `gcloud` CLI authenticated with Application Default Credentials (`gcloud auth application-default login`)
- Optional: Azure Application Insights resource for tracing

## Setup
1. Copy the provided environment template:
   ```bash
   cd gcp/vertex
   cp .env.example .env
   ```
2. Fill in the environment variables that the script reads (see `.env.example` for defaults):
   - `GOOGLE_CLOUD_PROJECT` (required) and `GOOGLE_CLOUD_REGION` (defaults to `us-central1`)
   - `VERTEX_MODEL_NAME` (defaults to `gemini-2.0-flash`)
   - `VERTEX_STAGING_BUCKET` and `VERTEX_GCS_DIR_NAME` for remote deployment
   - Optional Azure options: `APPLICATION_INSIGHTS_CONNECTION_STRING`, `VERTEX_AGENT_NAME`, `VERTEX_AGENT_ID`, `VERTEX_PROVIDER_NAME`
3. Install dependencies:
   ```bash
   pip install -r gcp/vertex/requirements.txt
   ```

## Running locally
Execute the script directly:
```bash
python gcp/vertex/vertex_langchain_agent.py
```
It performs the following steps:
1. Loads `.env`, validates `GOOGLE_CLOUD_PROJECT`, and initialises `vertexai`.
2. Builds a `LangchainAgent` with a single `get_exchange_rate` tool backed by the public Frankfurter API.
3. Wraps the runnable in `AzureAIOpenTelemetryTracer` when `APPLICATION_INSIGHTS_CONNECTION_STRING` is set.
4. Issues a sample query (“What is the exchange rate from US dollars to SEK today?”) and prints the response.

## Deploying to Agent Engines
- Set `VERTEX_STAGING_BUCKET=gs://...` in `.env`. If this variable is unset, deployment is skipped.
- Re-run the script. The `deploy_agent` helper packages the local agent (including `requirements.txt`) and calls `vertexai.Client().agent_engines.create`.
- Deployment metadata such as `VERTEX_AGENT_NAME` and `VERTEX_GCS_DIR_NAME` are read directly from the environment, so no code edits are needed.

## Telemetry
- `AzureAIOpenTelemetryTracer` comes from `langchain-azure-ai`. If the package or `APPLICATION_INSIGHTS_CONNECTION_STRING` is missing, the script logs that tracing is disabled and continues.
- Customize `VERTEX_AGENT_NAME`, `VERTEX_AGENT_ID`, and `VERTEX_PROVIDER_NAME` to control how spans are labeled in Application Insights.

## Tips
- Keep `.env` out of source control; store production credentials in Secret Manager or another vault.
- When running in CI or a managed environment, provide the same variables via the runtime environment instead of relying on `.env`.
