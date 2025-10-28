# Trace Evaluation

This guide demonstrates how to evaluate agent traces using Azure AI Project's evaluation capabilities. You can create evaluation groups and runs using either the REST API or Python SDK.

## Prerequisites

- Azure AI Project endpoint
- Application Insights resource with agent traces
- Azure credentials configured

> **⚠️ Important:** In order to get trace evaluation working, please make sure the **project Managed Identity (MI) has the "Log Analytics Reader" role** on the Application Insights instance that is associated with your project.

## Creating an Evaluation Group

An evaluation group defines the evaluators and data source configuration for your evaluations.

### REST API

**Endpoint:** `POST {{projectEndpoint}}/openai/evals?api-version=2025-11-15-preview`

**Request Body:**

```json
{
  "name": "trace-eval-group",
  "data_source_config": {
    "type": "azure_ai_source",
    "scenario": "traces"
  },
  "testing_criteria": [
    {
      "type": "azure_ai_evaluator",
      "name": "intent_resolution",
      "evaluator_name": "builtin.intent_resolution",
      "data_mapping": {
        "query": "{{query}}",
        "response": "{{response}}",
        "tool_definitions": "{{tool_definitions}}"
      },
      "initialization_parameters": {
        "deployment_name": "tiger5/gpt-4o-mini"
      }
    },
    {
      "type": "azure_ai_evaluator",
      "name": "task_adherence",
      "evaluator_name": "builtin.task_adherence",
      "data_mapping": {
        "query": "{{query}}",
        "response": "{{response}}",
        "tool_definitions": "{{tool_definitions}}"
      },
      "initialization_parameters": {
        "deployment_name": "tiger5/gpt-4o-mini"
      }
    },
    {
      "type": "azure_ai_evaluator",
      "name": "tool_call_accuracy",
      "evaluator_name": "builtin.tool_call_accuracy",
      "data_mapping": {
        "query": "{{query}}",
        "response": "{{response}}",
        "tool_definitions": "{{tool_definitions}}"
      },
      "initialization_parameters": {
        "deployment_name": "tiger5/gpt-4o-mini"
      }
    }
  ]
}
```

### Python SDK

```python
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

project_endpoint = "https://your-project.services.ai.azure.com/api/projects/yourProject"

with DefaultAzureCredential() as credential:
    with AIProjectClient(endpoint=project_endpoint, credential=credential, api_version="2025-11-15-preview") as project_client:
        client = project_client.get_openai_client()
        
        data_source_config = {
            "type": "azure_ai_source",
            "scenario": "traces"
        }
        
        testing_criteria = [
            {
                "type": "azure_ai_evaluator",
                "name": "intent_resolution",
                "evaluator_name": "builtin.intent_resolution",
                "data_mapping": {
                    "query": "{{query}}",
                    "response": "{{response}}",
                    "tool_definitions": "{{tool_definitions}}"
                },
                "initialization_parameters": {
                    "deployment_name": "tiger5/gpt-4o-mini"
                }
            },
            {
                "type": "azure_ai_evaluator",
                "name": "task_adherence",
                "evaluator_name": "builtin.task_adherence",
                "data_mapping": {
                    "query": "{{query}}",
                    "response": "{{response}}",
                    "tool_definitions": "{{tool_definitions}}"
                },
                "initialization_parameters": {
                    "deployment_name": "tiger5/gpt-4o-mini"
                }
            },
            {
                "type": "azure_ai_evaluator",
                "name": "tool_call_accuracy",
                "evaluator_name": "builtin.tool_call_accuracy",
                "data_mapping": {
                    "query": "{{query}}",
                    "response": "{{response}}",
                    "tool_definitions": "{{tool_definitions}}"
                },
                "initialization_parameters": {
                    "deployment_name": "tiger5/gpt-4o-mini"
                }
            }
        ]
        
        eval_object = client.evals.create(
            name="trace-eval-group",
            data_source_config=data_source_config,
            testing_criteria=testing_criteria,
        )
        
        print(f"Eval Group created with ID: {eval_object.id}")
```

## Creating an Evaluation Run

An evaluation run executes the evaluators on specific trace IDs.

### REST API

**Endpoint:** `POST {{projectEndpoint}}/openai/evals/{{eval_id}}/runs?api-version=2025-11-15-preview`

**Request Body:**

```json
{
  "name": "trace-eval-run",
  "data_source": {
    "type": "azure_ai_traces",
    "trace_ids": [
      "bd95398b24f9df44729b0546e869240a",
      "96bccc3581d2fe27aaca25a20ab7941f",
      "d2dc6966a9cc7726b207f201e64d0064"
    ],
    "lookback_hours": 400
  }
}
```

### Python SDK

```python
from datetime import datetime

# Assuming you have trace_ids from Application Insights query
trace_ids = [
    "bd95398b24f9df44729b0546e869240a",
    "96bccc3581d2fe27aaca25a20ab7941f",
    "d2dc6966a9cc7726b207f201e64d0064"
]

eval_run_object = client.evals.runs.create(
    eval_id=eval_object.id,
    name="trace-eval-run",
    data_source={
        "type": "azure_ai_traces",
        "trace_ids": trace_ids,
        "lookback_hours": 400
    }
)

print(f"Eval Run created with ID: {eval_run_object.id}")
print(f"Status: {eval_run_object.status}")
```

## Monitoring Evaluation Run

### Python SDK

```python
import time

print("\nMonitoring Eval Run status...")
while True:
    run = client.evals.runs.retrieve(run_id=eval_run_object.id, eval_id=eval_object.id)
    print(f"Status: {run.status}")
    
    if run.status == "completed" or run.status == "failed":
        print("\nEval Run finished!")
        print("Final Eval Run Response:")
        print(run)
        
        print("\nFetching output items...")
        output_items = list(client.evals.runs.output_items.list(
            run_id=run.id, eval_id=eval_object.id
        ))
        print(f"\nFound {len(output_items)} output items")
        break
        
    time.sleep(5)
    print("Waiting for eval run to complete...")
```

## Complete Example

See `trace_eval.py` for a complete example that:
1. Queries Application Insights for trace IDs based on agent ID and time range
2. Creates an evaluation group with built-in evaluators
3. Creates an evaluation run with the collected trace IDs
4. Monitors the evaluation run until completion

## Configuration

Create a `.env` file with your configuration:

```env
# Application Insights configuration
APPINSIGHTS_RESOURCE_ID=/subscriptions/{subscription-id}/resourceGroups/{resource-group}/providers/microsoft.insights/components/{app-insights-name}

# AI Project configuration
PROJECT_ENDPOINT=https://your-project.services.ai.azure.com/api/projects/yourProject
```

## Running the Example

```bash
# Install dependencies
pip install -r requirements.txt

# Run the trace evaluation script
python trace_eval.py
```
