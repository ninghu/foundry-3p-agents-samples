"""
Script to query Application Insights for trace IDs based on agent ID and time range.
"""
import os
import time
from datetime import datetime, timedelta, timezone
from pprint import pprint
from typing import Any

from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.monitor.query import LogsQueryClient, LogsQueryStatus
from azure.ai.projects import AIProjectClient

# Load environment variables from .env file
load_dotenv()


def _build_evaluator_config(name: str, evaluator_name: str) -> dict[str, Any]:
    """Create a standard Azure AI evaluator configuration block."""
    return {
        "type": "azure_ai_evaluator",
        "name": name,
        "evaluator_name": evaluator_name,
        "data_mapping": {
            "query": "{{query}}",
            "response": "{{response}}",
            "tool_definitions": "{{tool_definitions}}",
        },
        "initialization_parameters": {
            "deployment_name": "ninhuaiswedencentral/gpt-35-turbo",
        },
    }


def get_trace_ids(appinsight_resource_id: str, agent_id: str, start_time: datetime, end_time: datetime) -> list[str]:
    """
    Query Application Insights for trace IDs (operation_Id) based on agent ID and time range.
    
    Args:
        appinsight_resource_id: The resource ID of the Application Insights instance
        agent_id: The agent ID to filter by (e.g., "gcp-cloud-run-agent")
        start_time: Start time for the query
        end_time: End time for the query
    
    Returns:
        List of distinct operation IDs (trace IDs)
    """
    # Create credential and client
    credential = DefaultAzureCredential()
    client = LogsQueryClient(credential)
    
    # Build the KQL query
    query = f"""
    dependencies
    | where timestamp between (datetime({start_time.isoformat()}) .. datetime({end_time.isoformat()}))
    | extend agent_id = tostring(customDimensions["gen_ai.agent.id"])
    | where agent_id == "{agent_id}"
    | distinct operation_Id
    """
    
    try:
        # Execute the query
        response = client.query_resource(
            appinsight_resource_id,
            query=query,
            timespan=None  # Time range is specified in the query itself
        )
        
        # Check if query was successful
        if response.status == LogsQueryStatus.SUCCESS:
            trace_ids = []
            # Extract operation_Id from results
            for table in response.tables:
                for row in table.rows:
                    # operation_Id should be the first (and only) column
                    trace_ids.append(row[0])
            
            return trace_ids
        else:
            print(f"Query failed with status: {response.status}")
            if response.partial_error:
                print(f"Partial error: {response.partial_error}")
            return []
            
    except Exception as e:
        print(f"Error executing query: {e}")
        return []


def main():
    # Load configuration from environment variables
    appinsight_resource_id = os.getenv("APPINSIGHTS_RESOURCE_ID")
    project_endpoint = os.getenv("PROJECT_ENDPOINT")
    
    if not appinsight_resource_id:
        raise ValueError("APPINSIGHTS_RESOURCE_ID not found in environment variables")
    if not project_endpoint:
        raise ValueError("PROJECT_ENDPOINT not found in environment variables")

    # This is the agent id when you set up the azure tracer
    agent_id = "gcp-cloud-run-agent"
    
    # Use the most recent hour for trace analysis
    end_time = datetime.now(tz=timezone.utc)
    start_time = end_time - timedelta(hours=1)
    
    print(f"Querying Application Insights...")
    print(f"Agent ID: {agent_id}")
    print(f"Time range: {start_time} to {end_time}")
    
    trace_ids = get_trace_ids(appinsight_resource_id, agent_id, start_time, end_time)
    
    print(f"\nFound {len(trace_ids)} trace IDs:")
    for trace_id in trace_ids:
        print(f"  - {trace_id}")


    with DefaultAzureCredential() as credential:
        with AIProjectClient(endpoint=project_endpoint, credential=credential, api_version="2025-11-15-preview") as project_client:
            client = project_client.get_openai_client()
            data_source_config = {
                "type": "azure_ai_source",
                "scenario": "traces"
            }
            
            testing_criteria = [
                _build_evaluator_config(
                    name="intent_resolution",
                    evaluator_name="builtin.intent_resolution",
                ),
                _build_evaluator_config(
                    name="task_adherence",
                    evaluator_name="builtin.task_adherence",
                ),
            ]
            
            print("Creating Eval Group")
            eval_object = client.evals.create(
                name="agent_trace_eval_group",
                data_source_config=data_source_config,
                testing_criteria=testing_criteria,
            )
            print(f"Eval Group created")

            print("Get Eval Group by Id")
            eval_object_response = client.evals.retrieve(eval_object.id)
            print("Eval Group Response:")
            pprint(eval_object_response)

            print("\nCreating Eval Run with trace IDs")
            run_name = f"agent_trace_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            eval_run_object = client.evals.runs.create(
                eval_id=eval_object.id,
                name=run_name,
                metadata={
                    "agent_id": agent_id,
                    "start_time": start_time.isoformat(),
                    "end_time": end_time.isoformat()
                },
                data_source={
                    "type": "azure_ai_traces",
                    "trace_ids": trace_ids,
                    "lookback_hours": 400
                }
            )
            print(f"Eval Run created")
            pprint(eval_run_object)

            print("\nMonitoring Eval Run status...")
            while True:
                run = client.evals.runs.retrieve(run_id=eval_run_object.id, eval_id=eval_object.id)
                print(f"Status: {run.status}")
                
                if run.status == "completed" or run.status == "failed" or run.status == "canceled":
                    print("\nEval Run finished!")
                    print("Final Eval Run Response:")
                    pprint(run)
                    break
                    
                time.sleep(5)
                print("Waiting for eval run to complete...")

if __name__ == "__main__":
    main()
