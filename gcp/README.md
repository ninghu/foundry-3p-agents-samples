# GCP Vertex AI (LangChain)

Compact samples showing a LangChain agent on Vertex AI with traces sent to Azure Application Insights.

## Quick Start

- Install deps: `pip install -r gcp/requirements.txt`
- Authenticate: `gcloud auth application-default login`
- Update constants in `gcp/vertex_langchain_agent_v1.py` and `gcp/vertex_langchain_agent_v2.py` (`project`, `location`, `application_insights_connection_string`).
- Run v1 (callback tracer): `python gcp/vertex_langchain_agent_v1.py`
- Run v2 (custom builder): `python gcp/vertex_langchain_agent_v2.py`

## Notes

- Tracing uses `AzureAIOpenTelemetryTracer`.
- Keep `enable_tracing=False` on the Vertex agent to avoid conflicts with Azure tracer.
- Limitation: Cortex agent v1 tracing does not work for remote endpoints (Vertex Agent Engines), since the Azure tracer can only be set at query time.
