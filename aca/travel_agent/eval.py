"""
Trace-based evaluation for the ACA travel planner agent.

Queries Application Insights for trace IDs, then runs Azure AI built-in
evaluators (intent_resolution, task_adherence) against the collected traces.
"""

import argparse
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


def _build_evaluator_config(
    name: str, evaluator_name: str, deployment_name: str
) -> dict[str, Any]:
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
            "deployment_name": deployment_name,
        },
    }


def get_trace_ids(
    appinsight_resource_id: str,
    agent_id: str,
    start_time: datetime,
    end_time: datetime,
) -> list[str]:
    """
    Query Application Insights for trace IDs (operation_Id) based on agent ID and time range.

    Args:
        appinsight_resource_id: The resource ID of the Application Insights instance
        agent_id: The agent ID to filter by (e.g., "aca-travel-planner")
        start_time: Start time for the query
        end_time: End time for the query

    Returns:
        List of distinct operation IDs (trace IDs)
    """
    credential = DefaultAzureCredential()
    client = LogsQueryClient(credential)

    query = f"""
    dependencies
    | where timestamp between (datetime({start_time.isoformat()}) .. datetime({end_time.isoformat()}))
    | extend agent_id = tostring(customDimensions["gen_ai.agent.id"])
    | where agent_id == "{agent_id}"
    | distinct operation_Id
    """

    try:
        response = client.query_resource(
            appinsight_resource_id,
            query=query,
            timespan=None,  # Time range is specified in the query itself
        )

        if response.status == LogsQueryStatus.SUCCESS:
            trace_ids = []
            for table in response.tables:
                for row in table.rows:
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
    parser = argparse.ArgumentParser(
        description="Run trace-based evaluation for the ACA travel planner agent.",
    )
    parser.add_argument(
        "--generate-traffic",
        action="store_true",
        help="Generate traffic before running eval (calls generate_traffic.py).",
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8080",
        help="Agent URL for traffic generation (default: http://localhost:8080).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="Number of traffic requests to generate (default: 3).",
    )
    parser.add_argument(
        "--wait-minutes",
        type=int,
        default=5,
        help="Minutes to wait for trace propagation after traffic generation (default: 5).",
    )
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=1,
        help="Hours to look back for traces (default: 1).",
    )
    args = parser.parse_args()

    # Optionally generate traffic first
    if args.generate_traffic:
        from .generate_traffic import run as generate_run

        print(f"Generating {args.count} traffic request(s) to {args.url}...")
        generate_run(base_url=args.url, count=args.count, delay=2.0, timeout=120.0)
        print(f"\nWaiting {args.wait_minutes} minutes for traces to propagate...")
        time.sleep(args.wait_minutes * 60)

    # Load configuration from environment variables
    appinsight_resource_id = os.getenv("APPINSIGHTS_RESOURCE_ID")
    project_endpoint = os.getenv("PROJECT_ENDPOINT")
    agent_id = os.getenv("OTEL_AGENT_ID", "aca-travel-planner")
    eval_deployment = os.getenv("EVAL_DEPLOYMENT_NAME")

    if not appinsight_resource_id:
        raise ValueError("APPINSIGHTS_RESOURCE_ID not found in environment variables")
    if not project_endpoint:
        raise ValueError("PROJECT_ENDPOINT not found in environment variables")
    if not eval_deployment:
        raise ValueError(
            "EVAL_DEPLOYMENT_NAME not found in environment variables. "
            "Set it to the model deployment name (e.g. 'gpt-4.1')."
        )

    # Use the configured lookback window for trace analysis
    end_time = datetime.now(tz=timezone.utc)
    start_time = end_time - timedelta(hours=args.lookback_hours)

    print(f"Querying Application Insights...")
    print(f"Agent ID: {agent_id}")
    print(f"Time range: {start_time} to {end_time}")

    trace_ids = get_trace_ids(appinsight_resource_id, agent_id, start_time, end_time)

    print(f"\nFound {len(trace_ids)} trace IDs:")
    for trace_id in trace_ids:
        print(f"  - {trace_id}")

    if not trace_ids:
        print("\nNo traces found. Make sure you have generated traffic and waited")
        print("3-5 minutes for traces to propagate to Application Insights.")
        print("You can also try increasing --lookback-hours.")
        return

    with DefaultAzureCredential() as credential:
        with AIProjectClient(
            endpoint=project_endpoint,
            credential=credential,
            api_version="2025-11-15-preview",
        ) as project_client:
            client = project_client.get_openai_client()
            data_source_config = {
                "type": "azure_ai_source",
                "scenario": "traces",
            }

            testing_criteria = [
                _build_evaluator_config(
                    name="intent_resolution",
                    evaluator_name="builtin.intent_resolution",
                    deployment_name=eval_deployment,
                ),
                _build_evaluator_config(
                    name="task_adherence",
                    evaluator_name="builtin.task_adherence",
                    deployment_name=eval_deployment,
                ),
            ]

            print("Creating Eval Group")
            eval_object = client.evals.create(
                name="aca_travel_agent_trace_eval_group",
                data_source_config=data_source_config,
                testing_criteria=testing_criteria,
            )
            print("Eval Group created")

            print("Get Eval Group by Id")
            eval_object_response = client.evals.retrieve(eval_object.id)
            print("Eval Group Response:")
            pprint(eval_object_response)

            print("\nCreating Eval Run with trace IDs")
            run_name = f"aca_travel_agent_trace_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            eval_run_object = client.evals.runs.create(
                eval_id=eval_object.id,
                name=run_name,
                metadata={
                    "agent_id": agent_id,
                    "start_time": start_time.isoformat(),
                    "end_time": end_time.isoformat(),
                },
                data_source={
                    "type": "azure_ai_traces_preview",
                    "trace_ids": trace_ids,
                    "lookback_hours": 400,
                },
            )
            print("Eval Run created")
            pprint(eval_run_object)

            print("\nMonitoring Eval Run status...")
            while True:
                run = client.evals.runs.retrieve(
                    run_id=eval_run_object.id, eval_id=eval_object.id
                )
                print(f"Status: {run.status}")

                if run.status in ("completed", "failed", "canceled"):
                    print("\nEval Run finished!")
                    print("Final Eval Run Response:")
                    pprint(run)
                    break

                time.sleep(5)
                print("Waiting for eval run to complete...")


if __name__ == "__main__":
    main()
