"""LangGraph-powered currency exchange agent for Cloud Run."""

from __future__ import annotations

import logging
import os
from typing import Annotated, Any, Dict, List, Optional, TypedDict

import httpx
from langchain_core.tools import tool
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

try:
    from .tracing import configure_azure_tracer
except ImportError:  # pragma: no cover - fallback when running as a script
    from gcp.cloud_run_agent.tracing import configure_azure_tracer  # type: ignore

logger = logging.getLogger(__name__)

try:  # Optional dependency, resolved at runtime
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:  # pragma: no cover - optional
    ChatGoogleGenerativeAI = None  # type: ignore


class AgentConfigurationError(RuntimeError):
    """Raised when the agent is missing required configuration."""


@tool
def get_exchange_rate(
    currency_from: str = "USD",
    currency_to: str = "EUR",
    currency_date: str = "latest",
) -> Dict[str, Any]:
    """Retrieve an exchange rate between two currencies."""
    logger.info(
        "Fetching exchange rate",
        extra={
            "currency_from": currency_from,
            "currency_to": currency_to,
            "currency_date": currency_date,
        },
    )
    try:
        response = httpx.get(
            f"https://api.frankfurter.app/{currency_date}",
            params={"from": currency_from, "to": currency_to},
            timeout=httpx.Timeout(10.0),
        )
        response.raise_for_status()
        data = response.json()
        if "rates" not in data:
            raise ValueError("Frankfurter API returned malformed payload.")
        return data
    except httpx.TimeoutException as exc:  # pragma: no cover - network dependency
        raise RuntimeError("Frankfurter API timed out.") from exc
    except httpx.HTTPError as exc:  # pragma: no cover - network dependency
        raise RuntimeError(f"Frankfurter API request failed: {exc}") from exc


class AgentState(TypedDict):
    """Conversation state tracked by LangGraph."""

    messages: Annotated[List[Any], add_messages]


def _format_messages(user_message: str) -> List[Dict[str, str]]:
    """Return the conversation with a fixed system instruction."""
    system_prompt = (
        "You are a helpful assistant that only answers questions about currency "
        "exchange rates. Always choose the get_exchange_rate tool when you need "
        "fresh FX data. Decline unrelated requests politely."
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]


def _last_message_content(messages: List[Any]) -> str:
    """Extract the textual payload from the final LangChain message."""
    if not messages:
        return ""
    last_message = messages[-1]
    if hasattr(last_message, "content"):
        return str(last_message.content)
    if isinstance(last_message, dict):
        return str(last_message.get("content", ""))
    return str(last_message)


def _build_llm() -> Any:
    """Instantiate an LLM client based on environment variables."""
    if ChatGoogleGenerativeAI is None:
        raise AgentConfigurationError(
            "langchain-google-genai is not installed. Install the optional dependency.",
        )

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise AgentConfigurationError(
            "GOOGLE_API_KEY must be set to call the Gemini API.",
        )

    model = os.getenv("GOOGLE_MODEL_NAME")
    if not model:
        raise AgentConfigurationError(
            "GOOGLE_MODEL_NAME must be set to select a Gemini model.",
        )
    return ChatGoogleGenerativeAI(model=model, google_api_key=api_key)


def _build_graph() -> Any:
    """Compile the LangGraph workflow."""
    llm = _build_llm()
    llm_with_tools = llm.bind_tools([get_exchange_rate])

    graph_builder = StateGraph(AgentState)

    def call_model(state: AgentState) -> AgentState:
        response = llm_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    graph_builder.add_node("assistant", call_model)
    graph_builder.add_node("tools", ToolNode(tools=[get_exchange_rate]))
    graph_builder.add_conditional_edges("assistant", tools_condition)
    graph_builder.add_edge("tools", "assistant")
    graph_builder.add_edge(START, "assistant")

    return graph_builder.compile()


class ExchangeRateAgent:
    """LangGraph wrapper that exposes a synchronous `run` API."""

    def __init__(self, tracer: Optional[Any] = None):
        self._tracer = tracer or configure_azure_tracer()
        self._graph = _build_graph()
        self._callback_config: Optional[Dict[str, Any]] = None
        if self._tracer:
            self._callback_config = {"callbacks": [self._tracer]}

    def run(self, prompt: str) -> str:
        """Execute the agent synchronously and return the model response."""
        payload = {"messages": _format_messages(prompt)}
        config = self._callback_config

        try:
            result_state = (
                self._graph.invoke(payload)
                if not config
                else self._graph.invoke(payload, config=config)
            )
        except Exception as exc:  # pragma: no cover - runtime errors surfaced to API
            logger.exception("Agent invocation failed: %s", exc)
            raise

        return _last_message_content(result_state.get("messages", []))
