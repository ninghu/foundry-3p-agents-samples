# LangGraph Hierarchy Samples (GCP)

LangGraph demonstrations that showcase advanced graph routing patterns while emitting Azure Application Insights telemetry. Both scripts use Google Gemini (via `langchain-google-genai`) so they integrate smoothly with the rest of the GCP samples.

## Samples

1. **`hierarchical_supervisor.py`** – Supervisor agent coordinating nested child/grandchild LangGraph subgraphs. Highlights:
   - Supervisor delegates scheduling to a child graph that, in turn, calls a note-refinement grandchild graph.
   - Rich OpenTelemetry metadata for every agent/tool invocation.

2. **`nested_travel_planner.py`** – Travel planner where the synthesiser calls a nested inner agent through a tool.
   - Demonstrates tool-triggered nested agent execution.
   - Captures GenAI semantic conventions on root and nested spans.

## Setup

1. Copy the environment template and fill in credentials:
   ```bash
   cd gcp/langgraph_hierarchy
   cp .env.example .env
   ```
   Required values:
   - `GOOGLE_API_KEY`: Gemini API key.
   - `GOOGLE_MODEL_NAME`: Gemini model, e.g. `gemini-2.0-flash`.
   - `APPLICATION_INSIGHTS_CONNECTION_STRING`: Azure Application Insights connection string.

2. Install dependencies inside a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Run a sample:
   ```bash
   python hierarchical_supervisor.py
   python nested_travel_planner.py
   ```

Both scripts load `.env`, initialise Azure Monitor exporters, and emit spans compatible with GenAI semantic conventions. Open the Application Insights workspace to explore the resulting traces.
