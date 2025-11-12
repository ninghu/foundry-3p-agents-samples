# AWS Bedrock + LangGraph (AgentCore)

Currency-focused LangGraph workflow hosted inside an `BedrockAgentCoreApp`. The graph binds a single `get_exchange_rate` tool, routes prompts to a Bedrock Claude model, and optionally forwards spans to Azure Application Insights through `langchain-azure-ai`.

## Configuration knobs
All runtime settings live near the top of `aws/agent_core/agentcore_langgraph_agent.py`:
- `AWS_REGION` – where the Bedrock Runtime API is called (defaults to `us-west-2`).
- `BEDROCK_MODEL_ID` – model identifier passed to `ChatBedrock` (`anthropic.claude-3-5-sonnet-20240620-v1:0` by default).
- `APPLICATION_INSIGHTS_CONNECTION_STRING`, `AGENT_NAME`, `AGENT_ID`, `PROVIDER_NAME` – control Azure tracing.
- `SYSTEM_PROMPT` – short instruction the agent prepends to every conversation.

Update these constants (or load them from environment variables) before deploying to another account/region.

## Quick start
1. Install dependencies:
   ```bash
   pip install -r aws/agent_core/requirements.txt
   ```
2. Configure AWS credentials that can call `bedrock-runtime:InvokeModel` in `AWS_REGION` (e.g., `aws configure`, environment variables, or an assumed role).
3. Run the AgentCore app:
   ```bash
   python aws/agent_core/agentcore_langgraph_agent.py
   ```
   The `@app.entrypoint` named `invoke` accepts a JSON payload with a `prompt` field and returns `{"result": "<answer>"}`. When run locally, AgentCore exposes the HTTP server defined in `.bedrock_agentcore.yaml` (port 8080 by default).
4. (Optional) Build and run a container:
   ```bash
   docker build -t agentcore-aws aws/agent_core
   docker run --rm -p 8080:8080 agentcore-aws
   ```

## Tracing
- `AzureAIOpenTelemetryTracer` is attached automatically when the dependency is installed and `APPLICATION_INSIGHTS_CONNECTION_STRING` is non-empty.
- Set `AGENT_NAME`, `AGENT_ID`, and `PROVIDER_NAME` to meaningful values so that Application Insights can distinguish this workflow from the other samples.
- If tracing is disabled (missing package or connection string), the agent logs the reason and continues.

## Deployment notes
- `.bedrock_agentcore.yaml` is included as a starting point for hosting the app on AWS Agent Service. Make sure the service role and IAM permissions match your account.
- Move secrets (Azure connection string, model IDs) into a secure store such as AWS Secrets Manager before production deployments.
- `requests` to Frankfurter already include short timeouts and helpful error messages; feel free to replace this tool with something internal if you need enterprise data sources.
