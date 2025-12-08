from __future__ import annotations

from typing import Annotated, Any, Dict, List, Mapping, Optional

import boto3
import requests
from typing_extensions import TypedDict

from bedrock_agentcore import BedrockAgentCoreApp
from langchain_core.tools import tool
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition


import logging
logger = logging.getLogger(__name__)

try:
    from langchain_azure_ai.callbacks.tracers import AzureAIOpenTelemetryTracer  # type: ignore
except ImportError as exc:  # pragma: no cover - optional dependency
    logger.warning(
        "Failed to import langchain_azure_ai AzureAIOpenTelemetryTracer: %s. Tracing will be disabled.",
        exc,
    )
    AzureAIOpenTelemetryTracer = None  # type: ignore


from langchain_aws.chat_models import ChatBedrock as _BedrockChatModel

import os
from dotenv import load_dotenv

load_dotenv()

AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20240620-v1:0")
APPLICATION_INSIGHTS_CONNECTION_STRING = os.getenv("APPLICATION_INSIGHTS_CONNECTION_STRING")
AGENT_NAME = os.getenv("AGENT_NAME")
AGENT_ID = os.getenv("AGENT_ID")
PROVIDER_NAME = "aws.bedrock"
SYSTEM_PROMPT = (
    "You help users understand currency exchange rates and related context."
)


@tool
def get_exchange_rate(
    currency_from: str = "USD",
    currency_to: str = "EUR",
    currency_date: str = "latest",
) -> Dict[str, Any]:
    """Retrieve the exchange rate between two currencies on a specific date."""
    try:
        response = requests.get(
            f"https://api.frankfurter.app/{currency_date}",
            params={"base": currency_from, "symbols": currency_to},
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ValueError("Failed to retrieve exchange rate data") from exc
    return response.json()


class AgentState(TypedDict):
    messages: Annotated[List[Any], add_messages]


def _build_langgraph():
    bedrock_client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    llm = _BedrockChatModel(client=bedrock_client, model_id=BEDROCK_MODEL_ID)
    llm_with_tools = llm.bind_tools([get_exchange_rate])

    graph_builder = StateGraph(AgentState)

    def call_model(state: AgentState) -> AgentState:
        response = llm_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    graph_builder.add_node("assistant", call_model)

    tool_node = ToolNode(tools=[get_exchange_rate])
    graph_builder.add_node("tools", tool_node)
    graph_builder.add_conditional_edges("assistant", tools_condition)
    graph_builder.add_edge("tools", "assistant")
    graph_builder.add_edge(START, "assistant")

    return graph_builder.compile(name="aws currency exchange agent")


def _last_message_content(messages: List[Any]) -> str:
    if not messages:
        return ""
    last_message = messages[-1]
    if hasattr(last_message, "content"):
        return str(last_message.content)
    if isinstance(last_message, dict):
        return str(last_message.get("content", ""))
    return str(last_message)


def _create_graph_executor():
    tracer: Optional[Any] = None
    if AzureAIOpenTelemetryTracer is None:
        logger.warning(
            "langchain-azure-ai not installed; continuing without Azure Application Insights tracing.",
        )
    elif not APPLICATION_INSIGHTS_CONNECTION_STRING:
        logger.info(
            "APPLICATION_INSIGHTS_CONNECTION_STRING not provided; Azure tracing disabled.",
        )
    else:
        tracer = AzureAIOpenTelemetryTracer(
            connection_string=APPLICATION_INSIGHTS_CONNECTION_STRING,
            enable_content_recording=True,
            name=AGENT_NAME,
            agent_id=AGENT_ID,
            provider_name=PROVIDER_NAME,
        )
    graph = _build_langgraph()
    return graph, tracer


def _format_messages(user_message: str) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]


def _extract_trace_headers(context: Dict[str, Any] | None) -> Mapping[str, str]:
    """
    Extract W3C trace context headers from the incoming request context.
    
    This handles trace propagation from upstream services (API Gateway, 
    load balancers, or other services that pass traceparent/tracestate headers).
    """
    headers: Dict[str, str] = {}
    
    if not context:
        return headers
    
    # Check for traceparent in various possible locations
    # 1. Direct headers (e.g., from HTTP request)
    if "headers" in context:
        req_headers = context["headers"]
        if isinstance(req_headers, dict):
            # Case-insensitive header lookup
            for key, value in req_headers.items():
                lower_key = key.lower()
                if lower_key == "traceparent" and value:
                    headers["traceparent"] = value
                elif lower_key == "tracestate" and value:
                    headers["tracestate"] = value
    
    # 2. Direct context properties (some frameworks flatten headers)
    if "traceparent" in context:
        headers["traceparent"] = context["traceparent"]
    if "tracestate" in context:
        headers["tracestate"] = context["tracestate"]
    
    # 3. AWS Lambda/API Gateway specific context
    if "requestContext" in context:
        request_context = context["requestContext"]
        # Some API Gateway configurations include trace headers here
        if isinstance(request_context, dict):
            if "traceparent" in request_context:
                headers["traceparent"] = request_context["traceparent"]
    
    return headers


app = BedrockAgentCoreApp()
compiled_graph, azure_tracer = _create_graph_executor()


@app.entrypoint
def invoke(payload: Dict[str, Any], context: Dict[str, Any] | None = None) -> Dict[str, str]:
    """Invoke the LangGraph agent with the provided payload."""
    
    user_message = payload.get("prompt", "Hello! How can I help you today?")
    
    # Extract incoming trace headers from the request context
    trace_headers = _extract_trace_headers(context)
    
    try:
        graph_payload = {"messages": _format_messages(user_message)}
        config = {"callbacks": [azure_tracer]} if azure_tracer else {}
        
        if azure_tracer and trace_headers:
            # Propagate incoming trace context - this ensures the agent's spans
            # are children of the upstream service's span, maintaining the
            # distributed trace across service boundaries.
            with azure_tracer.use_propagated_context(headers=trace_headers):
                result_state = compiled_graph.invoke(graph_payload, config=config)
        else:
            result_state = compiled_graph.invoke(graph_payload, config=config)
            
        answer = _last_message_content(result_state.get("messages", []))
    except Exception as exc:  # pragma: no cover - agent runtime errors bubble up
        answer = f"Error while processing request: {exc}"
    return {"result": answer}


if __name__ == "__main__":
    app.run()
