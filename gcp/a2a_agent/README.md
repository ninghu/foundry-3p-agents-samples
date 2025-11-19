# Remote LangGraph Currency Agent

Remote A2A agent that wraps a LangGraph-powered currency assistant. The agent uses Gemini (`langchain_google_genai`) by default, exposes the A2A protocol via Starlette, protects endpoints with an API-key guard, and ships with Docker + Cloud Run deployment helpers.

## Prerequisites
- Python 3.12+ for local development
- Google Gemini API key **or** an OpenAI-compatible endpoint
- `gcloud` CLI + Google Cloud project (for Cloud Run builds)
- Docker (for local container runs)
- `.env` generated from `.env.example`

Optional:
- `evals/a2a/.env` for the evaluation script (`a2a_agent_eval.py`)
- Azure Application Insights resource + connection string if you want tracing.

## Configuration overview
`main.py` and `agent.py` read the following environment variables:

| Variable | Purpose |
| --- | --- |
| `model_source` | Set to `google` (default) to use Gemini, `azure` for Azure OpenAI, or any other string for an OpenAI-compatible endpoint. |
| `GOOGLE_API_KEY`, `GOOGLE_MODEL_NAME` | Required when `model_source=google`. |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_API_VERSION` | Preferred when `model_source=azure`. `AZURE_OPENAI_API_VERSION` falls back to `OPENAI_API_VERSION` or `2024-08-01-preview` (needed for structured outputs). `TOOL_LLM_URL`, `TOOL_LLM_NAME`, and `API_KEY` remain valid fallbacks for endpoint, deployment, and key. |
| `TOOL_LLM_URL`, `TOOL_LLM_NAME`, `API_KEY` | Required when using a generic OpenAI-compatible path. |
| `APPLICATION_INSIGHTS_CONNECTION_STRING`, `APPLICATION_INSIGHTS_AGENT_NAME`, `APPLICATION_INSIGHTS_AGENT_ID`, `APPLICATION_INSIGHTS_PROVIDER_NAME`, `APPLICATION_INSIGHTS_ENABLE_CONTENT` | Optional telemetry knobs. When the connection string is set (and `langchain-azure-ai[opentelemetry]` is installed), the agent attaches `AzureAIOpenTelemetryTracer` spans to every LangGraph invocation. |
| `A2A_AGENT_API_KEY` | Guards every request except `/`, `/healthz`, and `/_ah/health`. Provide via header `api-key` or query `api_key`. |
| `PUBLIC_HOST` / `PUBLIC_PORT` / `PUBLIC_SCHEME` | Controls the URLs advertised in the A2A agent card (defaults to whatever host:port the server binds to). |
| `BIND_HOST`, `HOST`, `PORT` | Override the server bind address when running locally or in containers. Cloud Run injects `PORT` automatically. |
| `GCP_PROJECT_ID`, `GCP_REGION` | Consumed by `deploy.py` to figure out build/deploy targets. |

See `.env.example` for a ready-to-copy template.

### Azure Application Insights tracing
Tracing is disabled by default. Set `APPLICATION_INSIGHTS_CONNECTION_STRING` (plus optional name/id/provider overrides) to enable `AzureAIOpenTelemetryTracer` and stream LangGraph spans into Azure Application Insights. Use `APPLICATION_INSIGHTS_ENABLE_CONTENT=false` if you prefer to redact prompt/response bodies from the traces.

## Local development
```bash
cp gcp/a2a_agent/.env.example gcp/a2a_agent/.env
# optional: cp evals/a2a/.env.example evals/a2a/.env

cd gcp/a2a_agent
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

python -m a2a_agent --host 127.0.0.1 --port 8080
```
The service responds to standard A2A endpoints (e.g. `/.well-known/a2a-agent-card`, `/invoke`, `/healthz`). Remember to supply the API key:
```bash
curl -H "api-key: ${A2A_AGENT_API_KEY}" http://localhost:8080/.well-known/a2a-agent-card
```

## Run with Docker locally
```bash
cd gcp/a2a_agent
docker build -t currency-agent .
docker run --rm -p 8080:8080 --env-file .env currency-agent
```

## Build & deploy to Cloud Run
```bash
cd gcp/a2a_agent

SERVICE_NAME=currency-agent
REGION=us-central1
PROJECT_ID=$(gcloud config get-value project)
IMAGE="us-central1-docker.pkg.dev/${PROJECT_ID}/agents/${SERVICE_NAME}:$(date +%Y%m%d%H%M%S)"

gcloud builds submit . --tag "${IMAGE}"
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars "$(tr '\n' ',' < .env | sed 's/,$//')"
```
Drop `--allow-unauthenticated` if you want to front the service with IAP or require Cloud Run IAM. Cloud Run supplies the `PORT` variable automatically, so no code changes are needed.

### Helper deploy script
`deploy.py` automates the Cloud Build + Cloud Run flow and reuses `.env` for configuration:
```powershell
cd gcp\a2a_agent
python deploy.py --env-file .\.env --service-name currency-agent --repo-name agents
```
Flags of note:
- `--image-tag` to pin a tag instead of the default timestamp.
- `--allow-unauthenticated` to expose the service publicly.
- `--extra-env KEY=VALUE,...` to append secrets that you do not want to keep in `.env`.

## Testing & evaluation
- Use `a2a-sdk` to drive the deployed agent:
  ```python
  from a2a.sdk import A2AClient
  client = A2AClient(base_url="https://<your-cloud-run-url>", api_key="<A2A_AGENT_API_KEY>")
  response = client.chat("What is the USD to EUR rate today?")
  print(response)
  ```
- `evals/a2a/a2a_agent_eval.py` demonstrates how to call the agent via an Azure AI connection, capture responses, and score them with the Task Adherence and Intent Resolution evaluators.

## Next steps
- Swap in a different toolset by editing `CurrencyAgent` (e.g., add FX hedging tips or pricing history).
- Persist the LangGraph checkpoint somewhere durable by replacing the in-memory `MemorySaver`.
- When publishing publicly, rotate `A2A_AGENT_API_KEY` regularly and consider adding rate limiting in front of the service.
