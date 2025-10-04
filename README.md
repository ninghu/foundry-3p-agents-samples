# Foundry Third-Party Agents Samples

This repository contains code samples demonstrating how to integrate third-party AI agent frameworks with Azure AI Foundry for observability and tracing.

## Overview

These samples showcase how to instrument agents from various frameworks to send telemetry data to Azure Application Insights using OpenTelemetry, enabling comprehensive monitoring and debugging of agent behavior.

## 📁 Repository Structure

```text
foundry-3p-agents-samples/
├── vertex_ai/          # Google Vertex AI LangChain agent samples
├── [future agents]/    # Additional third-party agent integrations
└── README.md
```

## 🚀 Available Samples

### Vertex AI (LangChain Agents)

Sample implementations demonstrating how to integrate Google Vertex AI's LangChain agents with Azure Application Insights for telemetry and tracing.

**Location:** `vertex_ai/`

**Features:**

- Currency exchange agent using LangChain and Vertex AI
- OpenTelemetry tracing integration with Azure Application Insights
- Two implementation approaches:
  - **v1**: Basic integration using callback configuration
  - **v2**: Advanced integration using custom runnable builder
- Agent deployment to Vertex AI with tracing enabled

**Key Components:**

- **Model**: Gemini 2.0 Flash
- **Tools**: Currency exchange rate lookup (Frankfurter API)
- **Tracing**: `AzureAIOpenTelemetryTracer` from `langchain-azure-ai`

#### Quick Start

1. **Install dependencies:**

   ```bash
   pip install -r vertex_ai/requirements.txt
   ```

2. **Authenticate with Google Cloud:**

   ```bash
   gcloud auth application-default login
   ```

3. **Configure your settings:**

   - Update `project`, `location`, and `application_insights_connection_string` in the agent files
   - Set your Azure Application Insights connection string

4. **Run the samples:**

   ```bash
   # Version 1 (callback-based tracing)
   python vertex_ai/langchain_agent_v1.py

   # Version 2 (custom runnable builder)
   python vertex_ai/langchain_agent_v2.py
   ```

#### Implementation Differences

**Version 1 (`langchain_agent_v1.py`):**

- Passes Azure tracer as a callback in the `query()` method
- Simpler setup, suitable for basic tracing needs
- Requires `enable_tracing=False` on the agent to avoid conflicts

**Version 2 (`langchain_agent_v2.py`):**

- Integrates Azure tracer directly in the custom runnable builder
- More robust for complex agent architectures
- Tracer is automatically applied to all agent executions