# Remote LangGraph Currency Agent

This folder contains a LangGraph-based A2A agent derived from the upstream [a2a-samples](https://github.com/a2aproject/a2a-samples/tree/main/samples/python/agents/langgraph) project. The agent exposes a currency conversion tool that relies on LangGraph, LangChain, and the A2A server runtime.

## Prerequisites

- Python 3.12 (for local runs)
- An API key for the backing LLM
  - `GOOGLE_API_KEY` for Gemini (default)
  - or `TOOL_LLM_URL`, `TOOL_LLM_NAME`, and `API_KEY` for an OpenAI-compatible endpoint
- Docker & gcloud CLIs (for Cloud Run)
- A configured GCP project with Cloud Run enabled
- A `gcp/a2a/remote_agent/.env` file (copy the adjacent `.env.example` and fill in the required values)
- Optional: a `gcp/a2a/.env` file for evaluator scripts (copy from `gcp/a2a/.env.example`)

## Local Development

```bash
cp gcp/a2a/remote_agent/.env.example gcp/a2a/remote_agent/.env
# optionally configure evaluation helpers
# cp gcp/a2a/.env.example gcp/a2a/.env

cd gcp/a2a/remote_agent
python -m venv .venv
source .venv/bin/activate  # on Windows use .venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt

python -m remote_agent --host 127.0.0.1 --port 8080
```

The server responds on `http://localhost:8080` and exposes the A2A protocol endpoints.
All requests must include the API key either via an `api-key` header or an `api_key` query parameter. For example:
```bash
curl -H "api-key: ${A2A_AGENT_API_KEY}" http://localhost:8080/.well-known/a2a-agent-card
```

### Run with Docker locally

```bash
cd gcp/a2a/remote_agent
docker build -t currency-agent .
docker run --rm -p 8080:8080 --env-file .env currency-agent
```

## Build & Deploy to Cloud Run

```bash
# Configure project/region if not already set
gcloud config set project YOUR_PROJECT_ID
gcloud config set run/region YOUR_REGION

cd gcp/a2a/remote_agent

SERVICE_NAME=currency-agent
IMAGE=YOUR_REGION-docker.pkg.dev/YOUR_PROJECT_ID/agents/${SERVICE_NAME}:$(date +%Y%m%d%H%M)

gcloud builds submit --tag "${IMAGE}"

gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars "$(tr '\n' ',' < .env | sed 's/,$//')"
```

Cloud Run automatically injects the `PORT` environment variable; the entrypoint reads it and listens on the correct port. If you need private access, remove `--allow-unauthenticated` and configure ingress/IAP as required.

### Using the helper deploy script

This directory also includes a cross-platform helper (`deploy.py`) that wraps Cloud Build and Cloud Run commands. The script reads configuration from `.env`, so populate `GCP_PROJECT_ID` and `GCP_REGION` alongside your other environment variables.

```powershell
cd gcp\a2a\remote_agent
python deploy.py --env-file .\.env
```

Optional arguments:

- `--service-name` (default `currency-agent`)
- `--repo-name` (Artifact Registry repo, default `agents`)
- `--image-tag` (defaults to UTC timestamp)
- `--extra-env` (comma-separated key/value pairs appended to Cloud Run env vars)

## Testing the Agent

You can exercise the deployed agent using the `a2a-sdk`:

```python
from a2a.sdk import A2AClient

client = A2AClient(base_url="https://<your-cloud-run-url>")
response = client.chat("What is the USD to EUR rate today?")
print(response)
```

This same client is used in `gcp/a2a/agent_eval.py` to drive evaluation runs. Refer to that script for Azure AI Evaluation integration.
