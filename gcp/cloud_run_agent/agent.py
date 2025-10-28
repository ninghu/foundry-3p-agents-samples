"""LangGraph-powered travel planner agent for Cloud Run."""

from __future__ import annotations

import atexit
import json
import logging
import os
import random
from datetime import datetime, timedelta
from typing import Annotated, Any, Callable, Dict, List, Optional, Sequence, TypedDict
from uuid import uuid4
from urllib.parse import urlparse

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import AnyMessage, add_messages

try:  # Optional dependency when exporting to Azure Monitor
    from azure.monitor.opentelemetry import (
        configure_azure_monitor as _configure_azure_monitor_impl,
    )
except ImportError:  # pragma: no cover - optional dependency
    _configure_azure_monitor_impl = None  # type: ignore

try:  # LangChain >= 1.0.0
    from langchain.agents import create_agent as _create_react_agent  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - compatibility with older LangGraph releases
    from langgraph.prebuilt import create_react_agent as _create_react_agent  # type: ignore[assignment]

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind

logger = logging.getLogger(__name__)

try:  # Optional dependency, resolved at runtime
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:  # pragma: no cover - optional
    ChatGoogleGenerativeAI = None  # type: ignore


class AgentConfigurationError(RuntimeError):
    """Raised when the agent is missing required configuration."""


# ---------------------------------------------------------------------------
# Telemetry helpers
# ---------------------------------------------------------------------------
_AZURE_MONITOR_CONFIGURED = False
_OTLP_EXPORTER_CONFIGURED = False
_TRACE_SHUTDOWN_REGISTERED = False


def _service_name() -> str:
    return os.getenv("OTEL_SERVICE_NAME", "gcp-cloud-run-travel-planner")


def _configure_azure_monitor_exporter() -> None:
    """Initialise Azure Monitor exporter if dependencies and configuration are present."""
    global _AZURE_MONITOR_CONFIGURED
    if _AZURE_MONITOR_CONFIGURED:
        return

    connection_string = os.getenv("APPLICATION_INSIGHTS_CONNECTION_STRING")
    if not connection_string:
        return

    if _configure_azure_monitor_impl is None:
        logger.warning(
            "azure-monitor-opentelemetry is not installed; skipping Azure Monitor exporter setup.",
        )
        return

    try:
        _configure_azure_monitor_impl(connection_string=connection_string)
    except Exception as exc:  # pragma: no cover - runtime dependency issues
        logger.warning("Failed to initialise Azure Monitor exporter: %s", exc)
        return

    _AZURE_MONITOR_CONFIGURED = True
    logger.info("Azure Monitor exporter configured.")


def _configure_otlp_exporter() -> None:
    """Attach an OTLP span exporter so root spans reach the configured backend."""
    global _OTLP_EXPORTER_CONFIGURED
    if _OTLP_EXPORTER_CONFIGURED:
        return

    try:
        exporter = OTLPSpanExporter()
    except Exception as exc:  # pragma: no cover - exporter misconfiguration
        logger.warning("Unable to initialise OTLP Span Exporter: %s", exc)
        return

    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        resource = Resource.create({"service.name": _service_name()})
        provider = TracerProvider(resource=resource)
        trace.set_tracer_provider(provider)

    provider.add_span_processor(BatchSpanProcessor(exporter))
    _OTLP_EXPORTER_CONFIGURED = True
    logger.info("OTLP span exporter configured.")


def _register_trace_shutdown_handlers() -> None:
    """Register process shutdown hooks so spans are flushed when the service stops."""
    global _TRACE_SHUTDOWN_REGISTERED
    if _TRACE_SHUTDOWN_REGISTERED:
        return

    def _shutdown_tracing() -> None:
        provider = trace.get_tracer_provider()
        flush = getattr(provider, "force_flush", None)
        if callable(flush):
            try:
                flush()
            except Exception as exc:  # pragma: no cover - diagnostic
                logger.debug("Tracer provider force_flush failed: %s", exc)

        shutdown = getattr(provider, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception as exc:  # pragma: no cover - diagnostic
                logger.debug("Tracer provider shutdown failed: %s", exc)

    atexit.register(_shutdown_tracing)
    _TRACE_SHUTDOWN_REGISTERED = True


def _flush_tracer_provider() -> None:
    """Flush spans to configured exporters."""
    provider = trace.get_tracer_provider()
    flush = getattr(provider, "force_flush", None)
    if callable(flush):
        try:
            flush()
        except Exception as exc:  # pragma: no cover - diagnostic
            logger.debug("Tracer provider force_flush failed: %s", exc)


def _configure_tracing_stack() -> None:
    """Ensure tracing exporters and shutdown handlers are configured exactly once."""
    _configure_azure_monitor_exporter()
    _configure_otlp_exporter()
    _register_trace_shutdown_handlers()

def _str_to_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def configure_azure_tracer() -> Optional[Any]:
    """Return an AzureAIOpenTelemetryTracer if configuration is present."""
    connection_string = os.getenv("APPLICATION_INSIGHTS_CONNECTION_STRING")
    if not connection_string:
        logger.info(
            "APPLICATION_INSIGHTS_CONNECTION_STRING not provided; skipping Azure tracing.",
        )
        return None

    try:
        from langchain_azure_ai.callbacks.tracers import AzureAIOpenTelemetryTracer  # type: ignore
    except ImportError:  # pragma: no cover - optional dependency
        logger.warning(
            "langchain-azure-ai >= 1.0.0 is required for Azure tracing; run without tracing.",
        )
        return None

    tracer = AzureAIOpenTelemetryTracer(
        connection_string=connection_string,
        enable_content_recording=_str_to_bool(
            os.getenv("APPLICATION_INSIGHTS_ENABLE_CONTENT"),
            default=True,
        ),
        name=os.getenv("APPLICATION_INSIGHTS_AGENT_NAME", "gcp-cloud-run-travel-planner"),
        agent_id=os.getenv("APPLICATION_INSIGHTS_AGENT_ID", "gcp-cloud-run-travel-planner"),
        provider_name=os.getenv("APPLICATION_INSIGHTS_PROVIDER_NAME", "gcp.cloud_run"),
    )

    logger.info("Azure Application Insights tracing enabled.")
    return tracer


# ---------------------------------------------------------------------------
# Sample data utilities
# ---------------------------------------------------------------------------
DESTINATIONS = {
    "paris": {
        "country": "France",
        "currency": "EUR",
        "airport": "CDG",
        "highlights": [
            "Eiffel Tower summit visit",
            "Seine River dinner cruise",
            "Day trip to Champagne region",
        ],
    },
    "tokyo": {
        "country": "Japan",
        "currency": "JPY",
        "airport": "HND",
        "highlights": [
            "Tsukiji market chef-led tour",
            "Evening in Golden Gai",
            "Bullet train excursion to Kyoto",
        ],
    },
    "rome": {
        "country": "Italy",
        "currency": "EUR",
        "airport": "FCO",
        "highlights": [
            "Underground Colosseum tour",
            "Hands-on pasta masterclass",
            "Sunset stroll in Trastevere",
        ],
    },
}


def _pick_destination(user_request: str) -> str:
    lowered = user_request.lower()
    for name in DESTINATIONS:
        if name in lowered:
            return name.title()
    return "Paris"


def _pick_origin(user_request: str) -> str:
    lowered = user_request.lower()
    for city in ["seattle", "new york", "san francisco", "london"]:
        if city in lowered:
            return city.title()
    return "Seattle"


def _compute_dates() -> tuple[str, str]:
    start = datetime.utcnow() + timedelta(days=30)
    end = start + timedelta(days=7)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Tools exposed to agents
# ---------------------------------------------------------------------------
@tool
def mock_search_flights(origin: str, destination: str, departure: str) -> str:
    """Return mock flight options for a given origin/destination pair."""
    random.seed(hash((origin, destination, departure)) % (2**32))
    airline = random.choice(["SkyLine", "AeroJet", "CloudNine"])
    fare = random.randint(700, 1250)
    return (
        f"Top choice: {airline} non-stop {origin}->{destination}, "
        f"depart {departure} 09:05, arrive 17:00. Premium fare ${fare} return."
    )


@tool
def mock_search_hotels(destination: str, check_in: str, check_out: str) -> str:
    """Return mock hotel recommendation for the stay."""
    random.seed(hash((destination, check_in, check_out)) % (2**32))
    name = random.choice(["Grand Meridian", "Hotel Lumiere", "The Atlas"])
    rate = random.randint(240, 410)
    return (
        f"{name} near the historic centre. Boutique suites, rooftop bar, "
        f"average nightly rate ${rate} including breakfast."
    )


@tool
def mock_search_activities(destination: str) -> str:
    """Return a short list of signature activities for the destination."""
    data = DESTINATIONS.get(destination.lower(), DESTINATIONS["paris"])
    bullets = "\n".join(f"- {item}" for item in data["highlights"])
    return f"Signature experiences in {destination.title()}:\n{bullets}"


# ---------------------------------------------------------------------------
# LangGraph state & helpers
# ---------------------------------------------------------------------------
class PlannerState(TypedDict):
    """Shared state that moves through the LangGraph workflow."""

    messages: Annotated[List[AnyMessage], add_messages]
    user_request: str
    session_id: str
    origin: str
    destination: str
    departure: str
    return_date: str
    travellers: int
    flight_summary: Optional[str]
    hotel_summary: Optional[str]
    activities_summary: Optional[str]
    final_itinerary: Optional[str]
    current_agent: str


def _model_name() -> str:
    return os.getenv("GOOGLE_MODEL_NAME", "models/gemini-1.5-pro")


def _resolve_server_attributes() -> tuple[str, Optional[int]]:
    base_url = os.getenv(
        "GOOGLE_API_BASE",
        "https://generativelanguage.googleapis.com",
    )
    normalized = base_url if "://" in base_url else f"https://{base_url}"
    parsed = urlparse(normalized)
    hostname = parsed.hostname or normalized.replace("https://", "").replace("http://", "").rstrip("/")
    return hostname, parsed.port


def _build_llm(agent_name: str, *, temperature: float) -> Any:
    """Instantiate an LLM client for a given agent."""
    if ChatGoogleGenerativeAI is None:
        raise AgentConfigurationError(
            "langchain-google-genai is not installed. Install the optional dependency.",
        )

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise AgentConfigurationError("GOOGLE_API_KEY must be set to call the Gemini API.")

    model = _model_name()
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=api_key,
        temperature=temperature,
        convert_system_message_to_user=True,
    )


def _agent_metadata(
    agent_name: str,
    *,
    session_id: str,
    temperature: float,
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "agent_name": agent_name,
        "thread_id": session_id,
        "session_id": session_id,
        "gen_ai.agent.name": agent_name,
        "gen_ai.agent.id": f"{agent_name}_{session_id}",
        "gen_ai.agent.description": agent_name.replace("_", " ").title(),
        "gen_ai.conversation.id": session_id,
        "gen_ai.request.model": _model_name(),
        "gen_ai.request.temperature": temperature,
        "gen_ai.request.top_p": 1.0,
        "gen_ai.request.max_tokens": 1024,
        "gen_ai.request.frequency_penalty": 0.0,
        "gen_ai.request.presence_penalty": 0.0,
        "gen_ai.provider.name": "gcp",
        "gen_ai.output.type": "text",
    }
    server_address, server_port = _resolve_server_attributes()
    metadata["service.name"] = _service_name()
    metadata["server.address"] = server_address
    if server_port is not None:
        metadata["server.port"] = server_port
    metadata["otel_agent_span"] = True
    metadata["otel_agent_span_allowed"] = ["AgentExecutor"]
    return metadata


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------
def _append_message(state: PlannerState, message: AnyMessage) -> str:
    """Append a message to shared state and return its textual content."""
    if isinstance(message, BaseMessage):
        content = str(message.content)
        state["messages"].append(message)
        return content
    content = str(message)
    state["messages"].append(AIMessage(content=content))
    return content


def _run_specialist(
    state: PlannerState,
    *,
    agent_name: str,
    temperature: float,
    tool_fn: Callable[..., Any],
    task: str,
    summary_key: str,
    next_agent: str,
    system_prompt: Optional[str] = None,
) -> PlannerState:
    """Execute a specialist agent node with shared behaviour."""
    llm = _build_llm(agent_name, temperature=temperature)
    agent = _create_react_agent(llm, tools=[tool_fn])
    metadata = _agent_metadata(
        agent_name,
        session_id=state["session_id"],
        temperature=temperature,
    )

    messages: List[BaseMessage] = []
    if system_prompt:
        messages.append(SystemMessage(content=system_prompt))
    messages.append(HumanMessage(content=task))

    result = agent.invoke(
        {"messages": messages},
        config={"metadata": metadata},
    )
    final_message = result["messages"][-1]
    summary = _append_message(state, final_message)

    state[summary_key] = summary
    state["current_agent"] = next_agent
    return state


# ---------------------------------------------------------------------------
# LangGraph nodes
# ---------------------------------------------------------------------------
def coordinator_node(state: PlannerState) -> PlannerState:
    llm = _build_llm("coordinator", temperature=0.2)
    system_message = SystemMessage(
        content=(
            "You are the lead travel coordinator. Extract the key details from the "
            "traveller's request and describe the plan for the specialist agents."
        ),
    )
    response = llm.invoke([system_message] + state["messages"])
    _append_message(state, response)
    state["current_agent"] = "flight_specialist"
    return state


def flight_specialist_node(state: PlannerState) -> PlannerState:
    task = (
        f"Find an appealing flight from {state['origin']} to {state['destination']} "
        f"departing {state['departure']} for {state['travellers']} travellers."
    )
    return _run_specialist(
        state,
        agent_name="flight_specialist",
        temperature=0.4,
        tool_fn=mock_search_flights,
        task=task,
        summary_key="flight_summary",
        next_agent="hotel_specialist",
        system_prompt=(
            "You are the flight specialist. You must call the mock_search_flights tool "
            "exactly once to gather the latest itinerary details before responding. "
            "After invoking the tool, summarise the returned option in 3-4 sentences."
        ),
    )


def hotel_specialist_node(state: PlannerState) -> PlannerState:
    task = (
        f"Recommend a boutique hotel in {state['destination']} between {state['departure']} "
        f"and {state['return_date']} for {state['travellers']} travellers."
    )
    return _run_specialist(
        state,
        agent_name="hotel_specialist",
        temperature=0.5,
        tool_fn=mock_search_hotels,
        task=task,
        summary_key="hotel_summary",
        next_agent="activity_specialist",
        system_prompt=(
            "You are the hotel specialist. Always call the mock_search_hotels tool exactly once "
            "to obtain boutique recommendations for the traveller's stay. Use the tool output to "
            "craft a concise summary highlighting property, vibe, and rate."
        ),
    )


def activity_specialist_node(state: PlannerState) -> PlannerState:
    task = f"Curate signature activities for travellers spending a week in {state['destination']}."
    return _run_specialist(
        state,
        agent_name="activity_specialist",
        temperature=0.6,
        tool_fn=mock_search_activities,
        task=task,
        summary_key="activities_summary",
        next_agent="plan_synthesizer",
        system_prompt=(
            "You are the activity specialist. You must call the mock_search_activities tool "
            "exactly once to retrieve curated experiences. Summarise the key highlights in a short list."
        ),
    )


def plan_synthesizer_node(state: PlannerState) -> PlannerState:
    llm = _build_llm("plan_synthesizer", temperature=0.3)
    system_prompt = SystemMessage(
        content=(
            "You are the travel plan synthesiser. Combine the specialist insights into a "
            "concise, structured itinerary covering flights, accommodation and activities."
        ),
    )
    summaries = json.dumps(
        {
            "flight": state["flight_summary"],
            "hotel": state["hotel_summary"],
            "activities": state["activities_summary"],
        },
    )
    response = llm.invoke(
        [
            system_prompt,
            HumanMessage(
                content=(
                    f"Traveller request: {state['user_request']}\n\n"
                    f"Origin: {state['origin']} | Destination: {state['destination']}\n"
                    f"Dates: {state['departure']} to {state['return_date']}\n\n"
                    f"Specialist summaries:\n{summaries}"
                ),
            ),
        ],
    )
    state["final_itinerary"] = _append_message(state, response)
    state["current_agent"] = "completed"
    return state


def should_continue(state: PlannerState) -> str:
    mapping = {
        "start": "coordinator",
        "coordinator": "flight_specialist",
        "flight_specialist": "hotel_specialist",
        "hotel_specialist": "activity_specialist",
        "activity_specialist": "plan_synthesizer",
    }
    return mapping.get(state["current_agent"], END)


def build_workflow() -> Any:
    graph = StateGraph(PlannerState)
    graph.add_node("coordinator", coordinator_node)
    graph.add_node("flight_specialist", flight_specialist_node)
    graph.add_node("hotel_specialist", hotel_specialist_node)
    graph.add_node("activity_specialist", activity_specialist_node)
    graph.add_node("plan_synthesizer", plan_synthesizer_node)
    graph.add_conditional_edges(START, should_continue)
    graph.add_conditional_edges("coordinator", should_continue)
    graph.add_conditional_edges("flight_specialist", should_continue)
    graph.add_conditional_edges("hotel_specialist", should_continue)
    graph.add_conditional_edges("activity_specialist", should_continue)
    graph.add_conditional_edges("plan_synthesizer", should_continue)
    return graph.compile()


# ---------------------------------------------------------------------------
# Execution helpers
# ---------------------------------------------------------------------------
def _serialize_messages(messages: Sequence[AnyMessage]) -> List[Dict[str, Any]]:
    serialised: List[Dict[str, Any]] = []
    for message in messages:
        role = "assistant"
        content = ""
        if isinstance(message, BaseMessage):
            role = message.type
            content = str(message.content)
        elif isinstance(message, dict):
            role = str(message.get("role", "assistant"))
            content = str(message.get("content", ""))
        else:
            content = str(message)
        serialised.append(
            {
                "role": role,
                "parts": [
                    {
                        "type": "text",
                        "content": content,
                    },
                ],
            },
        )
    return serialised


def _root_span_attributes(session_id: str) -> Dict[str, Any]:
    agent_name = os.getenv("TRAVEL_PLANNER_AGENT_NAME", "travel_multi_agent_planner")
    agent_id = os.getenv("TRAVEL_PLANNER_AGENT_ID", "gcp_cloud_run_travel_planner")
    agent_description = os.getenv(
        "TRAVEL_PLANNER_AGENT_DESCRIPTION",
        "LangGraph travel planner orchestrating multiple agents",
    )
    server_address, server_port = _resolve_server_attributes()
    service_name = _service_name()
    attributes: Dict[str, Any] = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.provider.name": "gcp",
        "gen_ai.request.model": _model_name(),
        "gen_ai.request.temperature": 0.4,
        "gen_ai.request.top_p": 1.0,
        "gen_ai.request.max_tokens": 1024,
        "gen_ai.request.frequency_penalty": 0.0,
        "gen_ai.request.presence_penalty": 0.0,
        "gen_ai.agent.name": agent_name,
        "gen_ai.agent.id": agent_id,
        "gen_ai.agent.description": agent_description,
        "gen_ai.conversation.id": session_id,
        "gen_ai.output.type": "text",
        "service.name": service_name,
        "server.address": server_address,
    }
    if server_port is not None:
        attributes["server.port"] = server_port
    return attributes


class TravelPlannerAgent:
    """LangGraph wrapper that exposes a synchronous `run` API for travel planning."""

    def __init__(self, tracer: Optional[Any] = None):
        _configure_tracing_stack()
        self._tracer = tracer or configure_azure_tracer()
        self._graph = build_workflow()

    @staticmethod
    def _build_config(session_id: str, tracer: Optional[Any]) -> Dict[str, Any]:
        config: Dict[str, Any] = {
            "configurable": {"thread_id": session_id},
            "metadata": {
                "session_id": session_id,
                "thread_id": session_id,
            },
            "recursion_limit": 10,
        }
        if tracer:
            config["callbacks"] = [tracer]
        return config

    def run(self, prompt: str) -> str:
        """Execute the travel planner synchronously and return the final itinerary."""
        session_id = str(uuid4())
        origin = _pick_origin(prompt)
        destination = _pick_destination(prompt)
        departure, return_date = _compute_dates()

        initial_state: PlannerState = {
            "messages": [HumanMessage(content=prompt)],
            "user_request": prompt,
            "session_id": session_id,
            "origin": origin,
            "destination": destination,
            "departure": departure,
            "return_date": return_date,
            "travellers": 2,
            "flight_summary": None,
            "hotel_summary": None,
            "activities_summary": None,
            "final_itinerary": None,
            "current_agent": "start",
        }

        config = self._build_config(session_id, self._tracer)
        tracer_impl = trace.get_tracer(__name__)
        root_attributes = _root_span_attributes(session_id)
        root_input = _serialize_messages(initial_state["messages"])

        try:
            with tracer_impl.start_as_current_span(
                name="travel_multi_agent_planner.invoke",
                kind=SpanKind.CLIENT,
                attributes=root_attributes,
            ) as root_span:
                root_span.set_attribute("gen_ai.input.messages", json.dumps(root_input))

                final_state = self._graph.invoke(initial_state, config=config)
                final_plan = final_state.get("final_itinerary") or ""

                if final_plan:
                    preview = final_plan[:500]
                    root_span.set_attribute(
                        "gen_ai.output.messages",
                        json.dumps(
                            [
                                {
                                    "role": "assistant",
                                    "parts": [{"type": "text", "content": preview}],
                                    "finish_reason": "stop",
                                },
                            ],
                        ),
                    )
                    root_span.set_attribute("metadata.final_plan.preview", preview)

                root_span.set_attribute("metadata.session_id", session_id)
                root_span.set_attribute(
                    "metadata.agents_used",
                    len(
                        [
                            key
                            for key in (
                                "flight_summary",
                                "hotel_summary",
                                "activities_summary",
                            )
                            if final_state.get(key)
                        ],
                    ),
                )
                root_span.set_attribute("gen_ai.response.model", _model_name())

                return final_plan
        except AgentConfigurationError:
            raise
        except Exception as exc:  # pragma: no cover - runtime errors surfaced to API
            logger.exception("Travel planner invocation failed: %s", exc)
            raise
        finally:
            _flush_tracer_provider()
