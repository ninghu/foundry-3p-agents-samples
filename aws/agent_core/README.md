# AWS Bedrock + LangGraph (AgentCore)

Simple currency-exchange agent running on AWS Bedrock via LangGraph/AgentCore, with optional traces sent to Azure Application Insights. Configuration is loaded from a `.env` file.

## Quick start
1. Copy the sample environment file and edit the placeholders:
   ```bash
   cd aws/agent_core
   cp .env.example .env
   ```
   Required values:
   - `AWS_REGION` and `BEDROCK_MODEL_ID` for the Bedrock model you plan to invoke.
   - Optional: `APPLICATION_INSIGHTS_CONNECTION_STRING` plus the `AGENT_*` overrides if you want Azure tracing metadata.

2. Install dependencies (from the repo root or a virtual environment):
   ```bash
   pip install -r aws/agent_core/requirements.txt
   ```

3. Configure AWS credentials (e.g., `aws configure`, environment variables, or an assumed role) that permit calling the Bedrock Runtime service.

4. Run the app locally:
   ```bash
   python aws/agent_core/agentcore_langgraph_agent.py
   ```
   The AgentCore application exposes the entrypoint defined with `@app.entrypoint` and can be invoked with a JSON payload containing `prompt`.

5. Optional – Docker build & run:
   ```bash
   docker build -t agentcore-aws aws/agent_core
   docker run --rm -p 8080:8080 --env-file aws/agent_core/.env agentcore-aws
   ```

## Tracing
- `AzureAIOpenTelemetryTracer` is enabled automatically when the optional dependency is installed and `APPLICATION_INSIGHTS_CONNECTION_STRING` is provided. Otherwise, the agent logs that tracing is disabled and continues normally.
- Customize `AGENT_NAME`, `AGENT_ID`, and `PROVIDER_NAME` in `.env` to control how telemetry appears in Application Insights.

## Deployment notes
- The included `.bedrock_agentcore.yaml` supplies a baseline runtime configuration. Update ARNs/IDs to match your AWS account before deploying.
- Keep sensitive values (Azure connection strings, customized prompts) in `.env` or a secret manager rather than hard-coding them.
