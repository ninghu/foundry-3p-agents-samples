"""LangGraph currency exchange agent for Cloud Run."""

from __future__ import annotations

import logging
import os
from typing import Annotated, Any, Dict, List, Mapping, Optional

import requests
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict

logger = logging.getLogger(__name__)

try:
    from langchain_azure_ai.callbacks.tracers import AzureAIOpenTelemetryTracer  # type: ignore
except ImportError as exc:  # pragma: no cover - optional dependency
    logger.warning(
        "Failed to import langchain_azure_ai AzureAIOpenTelemetryTracer: %s. Tracing will be disabled.",
        exc,
    )
    AzureAIOpenTelemetryTracer = None  # type: ignore


load_dotenv()

GOOGLE_MODEL_NAME = os.getenv("GOOGLE_MODEL_NAME", "gemini-2.0-flash")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You help users understand currency exchange rates and related context.",
)
APPLICATION_INSIGHTS_CONNECTION_STRING = os.getenv("APPLICATION_INSIGHTS_CONNECTION_STRING")
APPLICATION_INSIGHTS_AGENT_NAME = os.getenv("APPLICATION_INSIGHTS_AGENT_NAME", "gcp-cloud-run-currency-agent")
APPLICATION_INSIGHTS_AGENT_ID = os.getenv("APPLICATION_INSIGHTS_AGENT_ID", "gcp-cloud-run-currency-agent")
APPLICATION_INSIGHTS_PROVIDER_NAME = os.getenv("APPLICATION_INSIGHTS_PROVIDER_NAME", "gcp.cloud_run")
APPLICATION_INSIGHTS_ENABLE_CONTENT = os.getenv("APPLICATION_INSIGHTS_ENABLE_CONTENT", "true")


def _str_to_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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


def _build_llm() -> ChatGoogleGenerativeAI:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Set GOOGLE_API_KEY in your environment or .env file.")
    return ChatGoogleGenerativeAI(
        model=GOOGLE_MODEL_NAME,
        google_api_key=api_key,
        convert_system_message_to_user=True,
    )


def _build_langgraph():
    llm = _build_llm()
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

    return graph_builder.compile(name="gcp currency exchange agent")


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
        logger.info(
            "langchain-azure-ai not installed; continuing without Azure Application Insights tracing.",
        )
    elif not APPLICATION_INSIGHTS_CONNECTION_STRING:
        logger.info(
            "APPLICATION_INSIGHTS_CONNECTION_STRING not provided; Azure tracing disabled.",
        )
    else:
        tracer = AzureAIOpenTelemetryTracer(
            connection_string=APPLICATION_INSIGHTS_CONNECTION_STRING,
            enable_content_recording=_str_to_bool(APPLICATION_INSIGHTS_ENABLE_CONTENT, default=True),
            name=APPLICATION_INSIGHTS_AGENT_NAME,
            agent_id=APPLICATION_INSIGHTS_AGENT_ID,
            provider_name=APPLICATION_INSIGHTS_PROVIDER_NAME,
        )
        logger.info("Azure Application Insights tracing enabled.")
    graph = _build_langgraph()
    return graph, tracer


def _format_messages(user_message: str) -> List[Any]:
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_message),
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

    if "headers" in context:
        req_headers = context["headers"]
        if isinstance(req_headers, dict):
            for key, value in req_headers.items():
                lower_key = key.lower()
                if lower_key == "traceparent" and value:
                    headers["traceparent"] = value
                elif lower_key == "tracestate" and value:
                    headers["tracestate"] = value

    if "traceparent" in context and context.get("traceparent"):
        headers["traceparent"] = context["traceparent"]
    if "tracestate" in context and context.get("tracestate"):
        headers["tracestate"] = context["tracestate"]

    if "requestContext" in context:
        request_context = context["requestContext"]
        if isinstance(request_context, dict) and request_context.get("traceparent"):
            headers["traceparent"] = request_context["traceparent"]

    return headers


class ExchangeRateAgent:
    """Synchronous wrapper around the LangGraph currency agent."""

    def __init__(self) -> None:
        self._graph, self._tracer = _create_graph_executor()

    def run(self, prompt: str, context: Dict[str, Any] | None = None) -> str:
        """Invoke the agent with the provided prompt and optional request context."""
        user_message = prompt or "Hello! How can I help you today?"
        trace_headers = _extract_trace_headers(context)
        graph_payload = {"messages": _format_messages(user_message)}

        config: Dict[str, Any] = {}
        if self._tracer:
            config["callbacks"] = [self._tracer]

        try:
            if self._tracer and trace_headers:
                with self._tracer.use_propagated_context(headers=trace_headers):
                    result_state = self._graph.invoke(graph_payload, config=config)
            else:
                result_state = self._graph.invoke(graph_payload, config=config)
        except Exception as exc:  # pragma: no cover - runtime failures surfaced to caller
            logger.exception("Agent execution failed: %s", exc)
            raise

        return _last_message_content(result_state.get("messages", []))


__all__ = ["ExchangeRateAgent", "get_exchange_rate"]
