# Cloud Run Currency Agent (Simple)

Minimal FastAPI service that wraps the LangGraph currency exchange agent from `aws/agent_core/agentcore_langgraph_agent.py` and runs it on Google Cloud Run. The agent uses Gemini via `langchain_google_genai`, calls a Frankfurter API tool for exchange rates, and can stream spans to Azure Application Insights when configured.

## Prerequisites
- Python 3.12+
- Google Cloud project with Artifact Registry, Cloud Build, and Cloud Run enabled
- `gcloud` CLI installed/authenticated
- Google Generative AI (Gemini) API key
- Optional: Azure Application Insights connection string for tracing

## Configure environment variables
1. Copy the template and fill in values:
   ```bash
   cd gcp/cloud_run_agent_simple
   cp .env.example .env
   ```
2. Required:
   - `GCP_PROJECT_ID` / `GCP_REGION` for deployment.
   - `GOOGLE_API_KEY` for Gemini.
3. Optional:
   - `GOOGLE_MODEL_NAME` (defaults to `gemini-2.0-flash`).
   - `SYSTEM_PROMPT` to override the default currency helper prompt.
   - `APPLICATION_INSIGHTS_*` fields to enable Azure tracing.

`python-dotenv` loads `.env` for local runs and the deploy helper forwards the values to Cloud Run.

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
pip install -r gcp/cloud_run_agent_simple/requirements.txt
python -m gcp.cloud_run_agent_simple.main --host 0.0.0.0 --port 8080
```

Endpoints:
- `GET /healthz` - health probe (fails if the agent cannot start).
- `POST /invoke` - invoke the currency agent. Example:
  ```bash
  curl -s http://localhost:8080/invoke \
       -H "Content-Type: application/json" \
       -d '{"prompt": "What is the EUR to USD rate today?"}'
  ```
- `GET /` - landing message.

## Deploy to Cloud Run
Use the helper script (wraps Cloud Build + Cloud Run deploy):
```bash
python deploy.py \
  --env-file .env \
  --service-name currency-agent-simple \
  --repo-name agents \
  --allow-unauthenticated
```

Manual equivalent:
```bash
SERVICE=currency-agent-simple
REGION=us-central1
PROJECT_ID=$(gcloud config get-value project)
IMAGE_URI="$REGION-docker.pkg.dev/$PROJECT_ID/agents/$SERVICE:$(date +%Y%m%d%H%M%S)"

gcloud builds submit gcp/cloud_run_agent_simple --tag "$IMAGE_URI"
gcloud run deploy "$SERVICE" \
  --image "$IMAGE_URI" \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars "$(tr '\n' ',' < gcp/cloud_run_agent_simple/.env | sed 's/,$//')"
```
Drop `--allow-unauthenticated` to restrict access and move secrets to Secret Manager for production.

## Observability
- If `langchain-azure-ai` is installed and `APPLICATION_INSIGHTS_CONNECTION_STRING` is set, spans are emitted to Application Insights. Set `APPLICATION_INSIGHTS_ENABLE_CONTENT=false` to avoid recording bodies.
- The FastAPI route propagates `traceparent`/`tracestate` headers into the LangGraph callbacks so upstream traces continue inside the agent.

## What this sample mirrors
- Workflow structure, tool, and prompt logic match `aws/agent_core/agentcore_langgraph_agent.py`.
- Cloud Run layout, Dockerfile, and deploy helper mirror `gcp/cloud_run_agent` for parity across providers.
