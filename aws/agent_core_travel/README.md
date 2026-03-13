# AWS AgentCore Travel Planner with ADOT Telemetry

Multi-specialist travel planner running on **AWS Bedrock AgentCore** with **AWS Distro for OpenTelemetry (ADOT)** exporting traces to **Azure Application Insights**.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  BedrockAgentCoreApp (port 8080)                    │
│                                                     │
│  LangGraph: coordinator → flight → hotel →          │
│             activity → synthesizer                   │
│                                                     │
│  OTel SDK ──OTLP──► ADOT Collector (port 4317)     │
│                         │                           │
│                         ▼                           │
│                  Azure Monitor OTLP endpoint        │
│                  (Application Insights)             │
└─────────────────────────────────────────────────────┘
```

## Quick Start

1. **Copy environment config:**

   ```bash
   cp .env.example .env
   # Edit .env with your AWS credentials and Azure Monitor keys
   ```

2. **Parse your Application Insights connection string:**

   ```
   InstrumentationKey=<IKEY>;IngestionEndpoint=<ENDPOINT>;...
   ```

   Set in `.env`:
   - `AZURE_MONITOR_OTLP_ENDPOINT` = `<IngestionEndpoint>/v1/traces`
   - `AZURE_MONITOR_IKEY` = `<IKEY>`

3. **Run with Docker Compose:**

   ```bash
   docker-compose up --build
   ```

4. **Test:**

   ```bash
   curl -X POST http://localhost:8080/invoke \
     -H "Content-Type: application/json" \
     -d '{"prompt": "Plan a honeymoon from Seattle to Paris in November"}'
   ```

## Telemetry Flow

1. **Agent** (Python) creates OTel spans via `opentelemetry-sdk`
2. Spans are exported over **OTLP gRPC** to the **ADOT Collector** sidecar
3. ADOT Collector forwards traces to **Azure Monitor** via the `otlphttp` exporter
4. Traces appear in **Application Insights** > Transaction Search

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `AWS_REGION` | AWS region for Bedrock | `us-west-2` |
| `BEDROCK_MODEL_ID` | Bedrock model ID | `anthropic.claude-3-5-sonnet-20240620-v1:0` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | ADOT Collector endpoint | `http://localhost:4317` |
| `AZURE_MONITOR_OTLP_ENDPOINT` | Azure Monitor OTLP ingestion URL | — |
| `AZURE_MONITOR_IKEY` | Application Insights instrumentation key | — |
| `AGENT_NAME` | Agent display name | `aws-travel-planner-agent` |
| `AGENT_ID` | Agent identifier for trace filtering | `aws-agentcore-travel-planner` |

## Local Development (without Docker)

```bash
pip install -r requirements.txt
python -m agent
```

When running locally without the ADOT Collector, spans will fail to export silently. To test telemetry locally, run the ADOT Collector separately:

```bash
docker run --rm -p 4317:4317 -p 4318:4318 \
  -v $(pwd)/adot-collector-config.yaml:/etc/adot-config.yaml \
  --env-file .env \
  public.ecr.aws/aws-observability/aws-otel-collector:latest \
  --config /etc/adot-config.yaml
```
