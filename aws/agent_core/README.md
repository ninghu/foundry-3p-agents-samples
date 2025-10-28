# AWS Bedrock + LangGraph (AgentCore)

Simple currency-exchange agent running on AWS Bedrock via LangGraph/AgentCore, with optional traces sent to Azure Application Insights. Configuration is now defined directly in `agentcore_langgraph_agent.py`.

## Quick start
1. Review the configuration constants near the top of `aws/agent_core/agentcore_langgraph_agent.py` (`AWS_REGION`, `BEDROCK_MODEL_ID`, `APPLICATION_INSIGHTS_CONNECTION_STRING`, and the agent metadata) and adjust as needed for your environment.
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

5. Optional - Docker build & run:
   ```bash
   docker build -t agentcore-aws aws/agent_core
   docker run --rm -p 8080:8080 agentcore-aws
   ```

## Tracing
- `AzureAIOpenTelemetryTracer` is enabled automatically when the optional dependency is installed and `APPLICATION_INSIGHTS_CONNECTION_STRING` is populated in the module constants. Otherwise, the agent logs that tracing is disabled and continues normally.
- Customize `AGENT_NAME`, `AGENT_ID`, and `PROVIDER_NAME` in the module constants to control how telemetry appears in Application Insights.

## Deployment notes
- The included `.bedrock_agentcore.yaml` supplies a baseline runtime configuration. Update ARNs/IDs to match your AWS account before deploying.
- For production use, consider moving secrets (Azure connection strings, customized prompts) into a secret manager or environment variables instead of hard-coding them.
