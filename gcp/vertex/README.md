# Vertex AI LangChain Agent

Compact sample that runs a LangChain agent on Vertex AI Agent Engines and forwards traces to Azure Application Insights. Configuration is driven by environment variables loaded from a `.env` file.

## Quick start
1. Copy the sample environment file and edit the placeholders:
   ```bash
   cd gcp/vertex
   cp .env.example .env
   ```
   Required values:
   - `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_REGION` for the target Vertex AI project.
   - `VERTEX_STAGING_BUCKET` (e.g., `gs://your-bucket`) when packaging for remote deployment.
   - Optional: `APPLICATION_INSIGHTS_CONNECTION_STRING` to enable Azure tracing, plus `VERTEX_AGENT_*` overrides for metadata.

2. Install dependencies (from the repo root or a virtual environment):
   ```bash
   pip install -r gcp/vertex/requirements.txt
   ```

3. Authenticate with Google Cloud so the SDK can use Application Default Credentials:
   ```bash
   gcloud auth application-default login
   ```

4. Run the agent locally:
   ```bash
   python gcp/vertex/vertex_langchain_agent.py
   ```
   The script creates a local `LangchainAgent`, queries it once, and (optionally) deploys it if you keep the deployment call enabled.

## Deploying to Vertex AI Agent Engines
1. Ensure `VERTEX_STAGING_BUCKET` references a writable GCS bucket in the same project/region.
2. Ensure `VERTEX_STAGING_BUCKET` is set; if it’s absent the script runs locally and skips deployment.
3. Run the script again (the `deploy_agent` call is kept in the `__main__` block). Deployment uses the values from `.env` for naming and staging.

## Tracing
- Telemetry is emitted via `AzureAIOpenTelemetryTracer` when `APPLICATION_INSIGHTS_CONNECTION_STRING` is set. If it’s missing or the optional dependency is not installed, the agent continues without tracing.
- Adjust `VERTEX_AGENT_NAME`, `VERTEX_AGENT_ID`, and `VERTEX_PROVIDER_NAME` to control how traces appear in Application Insights.

## Authentication highlights
- Local runs and deployments rely on Application Default Credentials (`gcloud auth application-default login`). In managed environments, configure a service account with Vertex AI Agent Engines permissions.
- Keep sensitive values (Azure connection strings, staging bucket names) in `.env` or a secret manager; never hard-code them in source files.
