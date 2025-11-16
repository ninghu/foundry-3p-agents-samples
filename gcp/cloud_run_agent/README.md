# Cloud Run LangGraph Travel Planner

FastAPI service that hosts a multi-stage LangGraph travel planner on Google Cloud Run. The workflow orchestrates specialist agents (flights, hotels, activities, dining, plan synthesiser) backed by `langchain_google_genai` and can stream spans to Azure Application Insights via `langchain-azure-ai`.

## Prerequisites
- Python 3.12+
- Google Cloud project with Artifact Registry, Cloud Build, and Cloud Run enabled
- `gcloud` CLI installed/authenticated
- Google Generative AI (Gemini) API key that can call the chosen model
- Optional: Azure Application Insights resource for telemetry

## Configure environment variables
1. Copy the template and fill in the placeholders:
   ```bash
   cd gcp/cloud_run_agent
   cp .env.example .env
   ```
2. Required values:
   - `GCP_PROJECT_ID` / `GCP_REGION`: used by `deploy.py` to build and deploy.
   - `GOOGLE_API_KEY`: passed to `langchain_google_genai`.
3. Recommended overrides:
   - `GOOGLE_MODEL_NAME` (defaults to `gemini-2.0-flash`) and `GOOGLE_API_BASE` if you use a private endpoint.
   - `OTEL_SERVICE_NAME` and `OTEL_GENAI_PROVIDER` for telemetry metadata.
4. Azure tracing:
   - `APPLICATION_INSIGHTS_CONNECTION_STRING` enables `AzureAIOpenTelemetryTracer`.
   - `APPLICATION_INSIGHTS_AGENT_NAME`, `APPLICATION_INSIGHTS_AGENT_ID`, `APPLICATION_INSIGHTS_PROVIDER_NAME`, and `APPLICATION_INSIGHTS_ENABLE_CONTENT` fine-tune span labelling and prompt redaction.
5. Runtime knobs:
   - `HOST`/`BIND_HOST`/`PORT` override the FastAPI binding when running locally.

Anything in `.env` is loaded by `python-dotenv` (for local runs) and forwarded to Cloud Run by the helper deploy script.

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
pip install -r gcp/cloud_run_agent/requirements.txt
python -m gcp.cloud_run_agent.main --host 0.0.0.0 --port 8080
```

Available endpoints:
- `GET /healthz` – health probe (fails fast if the agent cannot start because of missing config).
- `POST /invoke` – run the travel planner. Example:
  ```bash
  curl -s http://localhost:8080/invoke \
       -H "Content-Type: application/json" \
       -d '{"prompt": "Plan a 4-day honeymoon from Seattle to Paris in November."}'
  ```
- `GET /` – simple landing message.

`main.py` eagerly instantiates `TravelPlannerAgent`, so missing `GOOGLE_API_KEY` or Gemini misconfiguration is surfaced at startup instead of per-request.

## Deploy to Cloud Run
### Helper script (recommended)
```bash
python deploy.py \
  --env-file .env \
  --service-name travel-planner-agent \
  --repo-name agents \
  --allow-unauthenticated
```
The script:
1. Parses the env file, pulling `GCP_PROJECT_ID`/`GCP_REGION`.
2. Builds a container with Cloud Build and publishes it to Artifact Registry (`{region}-docker.pkg.dev/<project>/<repo>/<service>:<timestamp>`).
3. Deploys to Cloud Run, forwarding every non-project/region variable in `.env` as `--set-env-vars`. Use `--extra-env KEY=VALUE,...` for overrides.

### Manual commands
```bash
SERVICE=travel-planner-agent
REGION=us-central1
PROJECT_ID=$(gcloud config get-value project)
IMAGE_URI="$REGION-docker.pkg.dev/$PROJECT_ID/agents/$SERVICE:$(date +%Y%m%d%H%M%S)"

gcloud builds submit gcp/cloud_run_agent --tag "$IMAGE_URI"
gcloud run deploy "$SERVICE" \
  --image "$IMAGE_URI" \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars "$(tr '\n' ',' < gcp/cloud_run_agent/.env | sed 's/,$//')"
```
Remove `--allow-unauthenticated` to lock down invocation and secure `GOOGLE_API_KEY` with Secret Manager for production deployments.

## Observability
- `TravelPlannerAgent` calls `_configure_tracer()` during startup. If `langchain-azure-ai` is missing or the connection string is blank, the service logs a warning and continues without tracing.
- Set `APPLICATION_INSIGHTS_ENABLE_CONTENT=false` to avoid recording prompt/response bodies in Application Insights.
- Each LangGraph node supplies metadata (agent name, span sources, session IDs) so the state machine is easy to follow inside the Azure portal.

## Extending the sample
- Add authentication (API Gateway, Cloud Endpoints, or Cloud Run IAM) before exposing the service publicly.
- Replace `langchain_google_genai.ChatGoogleGenerativeAI` with a different provider by implementing `_google_api_key`, `_model_name`, or the `_build_llm` helper in `agent.py`.
- Introduce additional specialists (e.g., budget or weather analysis) by adding nodes to `build_workflow`.
