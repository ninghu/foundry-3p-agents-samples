from __future__ import annotations

from typing import Annotated, Any, Dict, List

import boto3
import requests
from typing_extensions import TypedDict

from bedrock_agentcore import BedrockAgentCoreApp
from langchain_core.tools import tool
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition


from langchain_azure_ai.callbacks.tracers import AzureAIOpenTelemetryTracer  # type: ignore


from langchain_aws.chat_models import ChatBedrock as _BedrockChatModel

AWS_REGION = "us-west-2"
BEDROCK_MODEL_ID = "anthropic.claude-3-5-sonnet-20240620-v1:0"
APPLICATION_INSIGHTS_CONNECTION_STRING = "InstrumentationKey=e2d97709-3700-4cb2-97cd-0c9731012cd3;IngestionEndpoint=https://eastus-8.in.applicationinsights.azure.com/;LiveEndpoint=https://eastus.livediagnostics.monitor.azure.com/;ApplicationId=9e7ddef3-76bd-4af3-a9ff-4f0095502cfa"
AGENT_NAME = "aws-currency-exchange-agent"
AGENT_ID = f"aws-agent-7x9k2"
PROVIDER_NAME = "aws.bedrock"
SYSTEM_PROMPT = "You help users understand currency exchange rates and related context."


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

    return graph_builder.compile()


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
    tracer = AzureAIOpenTelemetryTracer(
        connection_string=APPLICATION_INSIGHTS_CONNECTION_STRING,
        enable_content_recording=True,
        name=AGENT_NAME,
        id=AGENT_ID,
        provider_name=PROVIDER_NAME,
    )
    graph = _build_langgraph()
    return graph, tracer


def _format_messages(user_message: str) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]


app = BedrockAgentCoreApp()
compiled_graph, azure_tracer = _create_graph_executor()


@app.entrypoint
def invoke(payload: Dict[str, Any], context: Dict[str, Any] | None = None) -> Dict[str, str]:
    """Invoke the LangGraph agent with the provided payload."""
    
    user_message = payload.get("prompt", "Hello! How can I help you today?")
    try:
        payload = {"messages": _format_messages(user_message)}
        if azure_tracer:
            result_state = compiled_graph.invoke(payload, config={"callbacks": [azure_tracer]})
        else:
            result_state = compiled_graph.invoke(payload)
        answer = _last_message_content(result_state.get("messages", []))
    except Exception as exc:  # pragma: no cover - agent runtime errors bubble up
        answer = f"Error while processing request: {exc}"
    return {"result": answer}


if __name__ == "__main__":
    app.run()

