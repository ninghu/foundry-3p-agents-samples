# Cloud Run LangGraph Exchange Agent

LangGraph sample that runs on Google Cloud Run and uses Azure Application Insights for tracing. The agent answers currency exchange questions by calling the public [Frankfurter](https://www.frankfurter.app/) API and routes LLM calls to the Google Generative AI (Gemini) API. Application Insights telemetry is captured via `AzureAIOpenTelemetryTracer` from `langchain-azure-ai>=1.0.0`. Environment variables are loaded from `.env` using `python-dotenv`.

## Prerequisites
- Google Cloud project with Cloud Run enabled.
- Google Cloud CLI (`gcloud`) installed and authenticated.
- Google Generative AI (Gemini) API key with access to the target model.
- Python 3.12+ for local runs.
- Azure Application Insights resource (optional but recommended for tracing). Copy the connection string from the Azure portal.

## Quick start
1. Copy the sample environment file and populate the values:
   ```bash
   cd gcp/cloud_run_agent
   cp .env.example .env
   ```
   Required entries:
   - `GCP_PROJECT_ID` and `GCP_REGION` for the deployment helper script.
   - `GOOGLE_API_KEY` and `GOOGLE_MODEL_NAME` for Gemini access (both required).
   - `APPLICATION_INSIGHTS_CONNECTION_STRING` for telemetry (leave blank to disable tracing).

2. Install dependencies and run the service locally:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   uvicorn cloud_run_agent.main:app --host 0.0.0.0 --port 8080 --reload
   ```
   The FastAPI docs UI becomes available at http://localhost:8080/docs.

3. Test the agent:
   ```bash
   curl -s -X POST http://localhost:8080/exchange \
        -H "Content-Type: application/json" \
        -d '{"prompt": "What is the USD to EUR rate today?"}'
   ```

## Deploy to Cloud Run
You can use the helper script or issue the `gcloud` commands manually.

### Via helper script
```bash
python deploy.py --env-file .env --service-name exchange-rate-agent --repo-name agents --allow-unauthenticated
```
The script performs a Cloud Build, pushes the container to Artifact Registry (`agents` repository), and deploys a Cloud Run service. Remove `--allow-unauthenticated` to require IAM authentication.

### Manual commands
```bash
SERVICE=exchange-rate-agent
REGION=us-central1
PROJECT_ID=$(gcloud config get-value project)
IMAGE_URI="$REGION-docker.pkg.dev/$PROJECT_ID/agents/$SERVICE:$(date +%Y%m%d%H%M%S)"

gcloud builds submit . --tag "$IMAGE_URI"
gcloud run deploy "$SERVICE" \
  --image "$IMAGE_URI" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars "$(tr '\n' ',' < .env | sed 's/,$//')"
```

## Configuration summary
- `GOOGLE_API_KEY`: required for calling the Gemini API. Store securely (e.g., Secret Manager).
- `GOOGLE_MODEL_NAME`: required model identifier for Gemini (e.g., `gemini-2.0-flash`).
- `APPLICATION_INSIGHTS_CONNECTION_STRING`: enables telemetry. Additional knobs (`APPLICATION_INSIGHTS_*`) customise the tracer metadata and content capture.
- Any variable in `.env` is propagated to Cloud Run by `deploy.py`.

## Authentication notes
- Supply `GOOGLE_API_KEY` via `.env` for local runs. For Cloud Run, prefer storing the key in Secret Manager and referencing it as an environment variable.
- The Cloud Run service account only needs access to read deployed environment variables and emit logs/metrics; no Vertex AI permissions are required.
- Azure Application Insights credentials should also be stored securely (e.g., Secret Manager) in production.

## Observability
- Telemetry is emitted through `AzureAIOpenTelemetryTracer`. If the package or connection string is missing, the app logs a warning and continues without tracing.
- Set `APPLICATION_INSIGHTS_ENABLE_CONTENT=false` to avoid storing prompt/response contents in Application Insights.

## Next steps
- Integrate Google Secret Manager and mount secrets as environment variables.
- Add request auth (e.g., API Gateway or Cloud Endpoints) if you disable `--allow-unauthenticated`.
- Extend the agent with additional finance tools or streaming responses.*** End Patch
