"""Nested multi-agent travel planner for the Cloud Run sample."""

from __future__ import annotations

import json
import logging
import os
import random
from datetime import datetime, timedelta
from typing import Annotated, Any, Dict, List, Optional, Sequence, TypedDict
from uuid import uuid4
from urllib.parse import urlparse

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import AnyMessage, add_messages
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool, tool

try:  # Optional dependency
    from azure.monitor.opentelemetry import configure_azure_monitor
except ImportError:  # pragma: no cover - optional dependency
    configure_azure_monitor = None  # type: ignore[assignment]

try:  # Optional dependency
    from langchain_azure_ai.callbacks.tracers import AzureAIOpenTelemetryTracer
except ImportError:  # pragma: no cover - optional dependency
    AzureAIOpenTelemetryTracer = None  # type: ignore[assignment]

try:  # Optional dependency resolved at runtime
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:  # pragma: no cover - optional dependency
    ChatGoogleGenerativeAI = None  # type: ignore[assignment]

try:  # LangChain >= 1.0.0
    from langchain.agents import create_agent as _create_react_agent  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - compatibility with older LangGraph releases
    from langgraph.prebuilt import create_react_agent as _create_react_agent  # type: ignore[assignment]


load_dotenv()

logger = logging.getLogger(__name__)


class AgentConfigurationError(RuntimeError):
    """Raised when the agent is missing required configuration."""


DESTINATIONS = {
    "paris": {
        "country": "France",
        "currency": "EUR",
        "airport": "CDG",
        "highlights": [
            "Eiffel Tower at sunset",
            "Seine dinner cruise",
            "Day trip to Versailles",
        ],
    },
    "tokyo": {
        "country": "Japan",
        "currency": "JPY",
        "airport": "HND",
        "highlights": [
            "Sushi masterclass in Tsukiji",
            "Ghibli Museum visit",
            "Day trip to Hakone hot springs",
        ],
    },
    "rome": {
        "country": "Italy",
        "currency": "EUR",
        "airport": "FCO",
        "highlights": [
            "Colosseum underground tour",
            "Private pasta masterclass",
            "Sunset walk through Trastevere",
        ],
    },
}


class PlannerState(TypedDict):
    """State shared across the LangGraph workflow."""

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
    dining_summary: Optional[str]
    final_itinerary: Optional[str]
    current_agent: str


TRACER: Optional[AzureAIOpenTelemetryTracer] = None


def _str_to_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _google_api_key() -> str:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise AgentConfigurationError("Set GOOGLE_API_KEY in your environment or .env file.")
    return api_key


def _model_name() -> str:
    return os.getenv("GOOGLE_MODEL_NAME", "gemini-2.0-flash")


def _provider_name() -> str:
    return os.getenv("OTEL_GENAI_PROVIDER", "google")


def _service_name() -> str:
    return os.getenv("OTEL_SERVICE_NAME", "nested-travel-sample")


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
    start = datetime.now() + timedelta(days=21)
    end = start + timedelta(days=5)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _resolve_server_attributes() -> tuple[str, int]:
    base_url = os.getenv("GOOGLE_API_BASE", "https://generativelanguage.googleapis.com")
    normalized = base_url if "://" in base_url else f"https://{base_url}"
    parsed = urlparse(normalized)
    server_address = parsed.hostname or normalized.replace("https://", "").rstrip("/")
    if parsed.port:
        return server_address, parsed.port
    return server_address, 80 if parsed.scheme == "http" else 443


def _configure_tracer() -> AzureAIOpenTelemetryTracer | None:
    connection_string = os.getenv("APPLICATION_INSIGHTS_CONNECTION_STRING")
    if not connection_string:
        logger.info("APPLICATION_INSIGHTS_CONNECTION_STRING not set; running without Azure tracing.")
        return None

    if configure_azure_monitor is None or AzureAIOpenTelemetryTracer is None:
        logger.warning(
            "Azure tracing dependencies not installed; skipping Application Insights telemetry.",
        )
        return None

    configure_azure_monitor(connection_string=connection_string)
    tracer = AzureAIOpenTelemetryTracer(
        connection_string=connection_string,
        enable_content_recording=_str_to_bool(os.getenv("APPLICATION_INSIGHTS_ENABLE_CONTENT"), default=True),
        name=os.getenv("APPLICATION_INSIGHTS_AGENT_NAME", "nested-travel-planner"),
        agent_id=os.getenv("APPLICATION_INSIGHTS_AGENT_ID", "nested-travel-planner"),
        provider_name=_provider_name(),
    )
    logger.info("Azure Application Insights tracing enabled.")
    return tracer


def _create_llm(agent_name: str, *, temperature: float, session_id: str) -> ChatGoogleGenerativeAI:
    if ChatGoogleGenerativeAI is None:  # pragma: no cover - optional dependency
        raise AgentConfigurationError(
            "Install langchain-google-genai to run the travel planner agent.",
        )

    tags = [f"agent:{agent_name}", "nested-travel-sample"]
    metadata = {
        "agent_name": agent_name,
        "agent_type": agent_name,
        "session_id": session_id,
        "thread_id": session_id,
        "ls_model_name": _model_name(),
        "ls_temperature": temperature,
    }
    return ChatGoogleGenerativeAI(
        model=_model_name(),
        google_api_key=_google_api_key(),
        temperature=temperature,
        convert_system_message_to_user=True,
        tags=tags,
        metadata=metadata,
    )


def _agent_metadata(
    agent_name: str,
    *,
    session_id: str,
    temperature: float,
    agent_description: str | None = None,
    span_sources: Sequence[str] | None = None,
) -> dict[str, Any]:
    server_address, server_port = _resolve_server_attributes()
    description = agent_description or agent_name.replace("_", " ").title()
    metadata: dict[str, Any] = {
        "agent_name": agent_name,
        "agent_id": f"{agent_name}_{session_id}",
        "agent_description": description,
        "otel_agent_span": True,
        "langgraph_node": agent_name,
        "thread_id": session_id,
        "session_id": session_id,
        "gen_ai.agent.name": agent_name,
        "gen_ai.agent.id": f"{agent_name}_{session_id}",
        "gen_ai.agent.description": description,
        "gen_ai.provider.name": _provider_name(),
        "gen_ai.request.model": _model_name(),
        "gen_ai.request.temperature": temperature,
        "gen_ai.request.top_p": 1.0,
        "gen_ai.request.max_tokens": 1024,
        "gen_ai.request.frequency_penalty": 0.0,
        "gen_ai.request.presence_penalty": 0.0,
        "gen_ai.conversation.id": session_id,
        "gen_ai.output.type": "text",
        "server.address": server_address,
        "server.port": server_port,
        "service.name": _service_name(),
    }
    metadata["otel_agent_span_allowed"] = list(span_sources or ("AgentExecutor",))
    return metadata


def _invoke_config(metadata: dict[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = {"metadata": metadata}
    if TRACER:
        config["callbacks"] = [TRACER]
    return config


@tool
def mock_search_flights(origin: str, destination: str, departure: str) -> str:
    """Return a synthetic flight option for the supplied route and date."""
    random.seed(hash((origin, destination, departure)) % (2**32))
    airline = random.choice(["SkyLine", "AeroJet", "CloudNine"])
    fare = random.randint(700, 1250)
    return (
        f"Top choice: {airline} non-stop {origin}->{destination}, depart {departure} 09:05, "
        f"arrive 16:55. Premium economy fare ${fare} return."
    )


@tool
def mock_search_hotels(destination: str, check_in: str, check_out: str) -> str:
    """Return a synthetic boutique hotel option."""
    random.seed(hash((destination, check_in, check_out)) % (2**32))
    name = random.choice(["Maison Azure", "Le Jardin", "Vista Royale"])
    rate = random.randint(220, 380)
    return (
        f"{name} near the historic centre. Chic suites, rooftop lounge, "
        f"average nightly rate ${rate} with breakfast."
    )


@tool
def mock_search_activities(destination: str) -> str:
    """Return highlight activities for the destination."""
    data = DESTINATIONS.get(destination.lower(), DESTINATIONS["paris"])
    bullets = "\n".join(f"- {item}" for item in data["highlights"])
    return f"Signature experiences in {destination.title()}:\n{bullets}"


@tool
def mock_search_dining(destination: str) -> str:
    """Return dining experiences for the destination."""
    picks = {
        "paris": [
            "Le Jardin Secret – seasonal tasting menu in Le Marais",
            "Chez Camille – convivial bistro near Canal Saint-Martin",
            "Nuage – rooftop cocktails with Eiffel Tower views",
        ],
        "tokyo": [
            "Sora Sushi – omakase with Tsukiji market fish",
            "Yakitori Kobo – late-night grill in Shinjuku",
            "Momiji Kaiseki – Kyoto-style seasonal courses",
        ],
        "rome": [
            "Trattoria del Colosseo – handmade pasta near the Forum",
            "Mercato Centrale – gourmet food hall tastings",
            "Il Tramonto – rooftop aperitivo overlooking Trastevere",
        ],
    }
    restaurants = picks.get(destination.lower(), picks["paris"])
    random.seed(hash((destination, "dining")) % (2**32))
    chosen = random.sample(restaurants, k=min(3, len(restaurants)))
    bullets = "\n".join(f"- {item}" for item in chosen)
    return f"Dining highlights in {destination.title()}:\n{bullets}"


def coordinator_node(state: PlannerState) -> PlannerState:
    llm = _create_llm("coordinator", temperature=0.2, session_id=state["session_id"])
    system_message = SystemMessage(
        content=(
            "You are the lead travel coordinator. Extract the key details from the "
            "traveller's request and describe the plan for the specialist agents."
        ),
    )
    response = llm.invoke([system_message] + state["messages"])
    state["messages"].append(response)
    state["current_agent"] = "flight_specialist"
    return state


def flight_specialist_node(state: PlannerState) -> PlannerState:
    llm = _create_llm("flight_specialist", temperature=0.4, session_id=state["session_id"])
    agent = _create_react_agent(llm, tools=[mock_search_flights])
    task = (
        f"Find an appealing flight from {state['origin']} to {state['destination']} "
        f"departing {state['departure']} for {state['travellers']} travellers."
    )
    metadata = _agent_metadata(
        "flight_specialist",
        session_id=state["session_id"],
        temperature=0.4,
        agent_description="Flight specialist agent",
        span_sources=("AgentExecutor",),
    )
    result = agent.invoke({"messages": [HumanMessage(content=task)]}, config=_invoke_config(metadata))
    final_message = result["messages"][-1]
    state["flight_summary"] = final_message.content if isinstance(final_message, BaseMessage) else str(final_message)
    state["messages"].append(
        final_message if isinstance(final_message, BaseMessage) else AIMessage(content=str(final_message)),
    )
    state["current_agent"] = "hotel_specialist"
    return state


def hotel_specialist_node(state: PlannerState) -> PlannerState:
    llm = _create_llm("hotel_specialist", temperature=0.5, session_id=state["session_id"])
    agent = _create_react_agent(llm, tools=[mock_search_hotels])
    task = (
        f"Recommend a boutique hotel in {state['destination']} between {state['departure']} "
        f"and {state['return_date']} for {state['travellers']} travellers."
    )
    metadata = _agent_metadata(
        "hotel_specialist",
        session_id=state["session_id"],
        temperature=0.5,
        agent_description="Hotel specialist agent",
        span_sources=("AgentExecutor",),
    )
    result = agent.invoke({"messages": [HumanMessage(content=task)]}, config=_invoke_config(metadata))
    final_message = result["messages"][-1]
    state["hotel_summary"] = final_message.content if isinstance(final_message, BaseMessage) else str(final_message)
    state["messages"].append(
        final_message if isinstance(final_message, BaseMessage) else AIMessage(content=str(final_message)),
    )
    state["current_agent"] = "activity_specialist"
    return state


def activity_specialist_node(state: PlannerState) -> PlannerState:
    llm = _create_llm("activity_specialist", temperature=0.6, session_id=state["session_id"])
    agent = _create_react_agent(llm, tools=[mock_search_activities])
    task = f"Curate signature activities for travellers spending a week in {state['destination']}."
    metadata = _agent_metadata(
        "activity_specialist",
        session_id=state["session_id"],
        temperature=0.6,
        agent_description="Activity specialist agent",
        span_sources=("AgentExecutor",),
    )
    result = agent.invoke({"messages": [HumanMessage(content=task)]}, config=_invoke_config(metadata))
    final_message = result["messages"][-1]
    state["activities_summary"] = (
        final_message.content if isinstance(final_message, BaseMessage) else str(final_message)
    )
    state["messages"].append(
        final_message if isinstance(final_message, BaseMessage) else AIMessage(content=str(final_message)),
    )
    state["current_agent"] = "dining_specialist"
    return state


def dining_specialist_node(state: PlannerState) -> PlannerState:
    llm = _create_llm("dining_specialist", temperature=0.4, session_id=state["session_id"])
    agent = _create_react_agent(llm, tools=[mock_search_dining])
    task = f"Recommend dining highlights for a week-long stay in {state['destination']}."
    metadata = _agent_metadata(
        "dining_specialist",
        session_id=state["session_id"],
        temperature=0.4,
        agent_description="Dining specialist agent",
        span_sources=("AgentExecutor",),
    )
    result = agent.invoke({"messages": [HumanMessage(content=task)]}, config=_invoke_config(metadata))
    final_message = result["messages"][-1]
    state["dining_summary"] = final_message.content if isinstance(final_message, BaseMessage) else str(final_message)
    state["messages"].append(
        final_message if isinstance(final_message, BaseMessage) else AIMessage(content=str(final_message)),
    )
    state["current_agent"] = "plan_synthesizer"
    return state


def _invoke_inner_summary_agent(session_id: str, payload: dict[str, Optional[str]]) -> str:
    llm = _create_llm("itinerary_editor", temperature=0.2, session_id=session_id)
    nested_agent = _create_react_agent(llm, tools=[])
    metadata = _agent_metadata(
        "itinerary_editor",
        session_id=session_id,
        temperature=0.2,
        agent_description="Inner agent that polishes the itinerary draft",
        span_sources=("AgentExecutor",),
    )
    result = nested_agent.invoke(
        {"messages": [HumanMessage(content=f"Refine this travel plan:\n{json.dumps(payload, indent=2)}")]},
        config=_invoke_config(metadata),
    )
    message = result["messages"][-1]
    return message.content if isinstance(message, BaseMessage) else str(message)


def _build_polish_tool(session_id: str, summary_payload: dict[str, Optional[str]]) -> StructuredTool:
    def polish_itinerary(draft: str) -> str:
        """Polish the itinerary draft using a nested agent."""
        payload = dict(summary_payload)
        payload["draft"] = draft
        return _invoke_inner_summary_agent(session_id, payload)

    return StructuredTool.from_function(
        polish_itinerary,
        name="polish_itinerary",
        description=(
            "Use to refine the itinerary before returning it to the traveller. "
            "Provide the full itinerary text via the 'draft' argument."
        ),
    )


def plan_synthesizer_node(state: PlannerState) -> PlannerState:
    llm = _create_llm("plan_synthesizer", temperature=0.3, session_id=state["session_id"])
    summaries = {
        "flight": state["flight_summary"],
        "hotel": state["hotel_summary"],
        "activities": state["activities_summary"],
        "dining": state["dining_summary"],
    }
    metadata = _agent_metadata(
        "plan_synthesizer",
        session_id=state["session_id"],
        temperature=0.3,
        agent_description="Plan synthesiser agent",
        span_sources=("AgentExecutor",),
    )

    polish_tool = _build_polish_tool(state["session_id"], summaries)
    plan_agent = _create_react_agent(llm, tools=[polish_tool])
    agent_prompt = (
        "You combine specialist outputs into a polished travel itinerary.\n"
        "Steps:\n"
        "1. Draft a detailed itinerary using the information provided.\n"
        "2. Call the tool `polish_itinerary` exactly once with the full draft text.\n"
        "3. Output only the polished itinerary returned by the tool.\n\n"
        f"Traveller request:\n{state['user_request']}\n\n"
        f"Origin: {state['origin']} | Destination: {state['destination']}\n"
        f"Dates: {state['departure']} to {state['return_date']}\n\n"
        f"Specialist summaries:\n{json.dumps(summaries, indent=2)}"
    )

    result = plan_agent.invoke({"messages": [HumanMessage(content=agent_prompt)]}, config=_invoke_config(metadata))
    final_message = result["messages"][-1]
    final_text = final_message.content if isinstance(final_message, BaseMessage) else str(final_message)
    state["final_itinerary"] = final_text
    state["messages"].append(
        final_message if isinstance(final_message, BaseMessage) else AIMessage(content=final_text),
    )
    state["current_agent"] = "completed"
    return state


def should_continue(state: PlannerState) -> str:
    mapping = {
        "start": "coordinator",
        "flight_specialist": "flight_specialist",
        "hotel_specialist": "hotel_specialist",
        "activity_specialist": "activity_specialist",
        "dining_specialist": "dining_specialist",
        "plan_synthesizer": "plan_synthesizer",
    }
    return mapping.get(state["current_agent"], END)


def build_workflow() -> StateGraph:
    graph = StateGraph(PlannerState, name ="TravelPlannerWorkflow")
    graph.add_node("coordinator", coordinator_node)
    graph.add_node("flight_specialist", flight_specialist_node)
    graph.add_node("hotel_specialist", hotel_specialist_node)
    graph.add_node("activity_specialist", activity_specialist_node)
    graph.add_node("dining_specialist", dining_specialist_node)
    graph.add_node("plan_synthesizer", plan_synthesizer_node)
    graph.add_conditional_edges(START, should_continue)
    graph.add_conditional_edges("coordinator", should_continue)
    graph.add_conditional_edges("flight_specialist", should_continue)
    graph.add_conditional_edges("hotel_specialist", should_continue)
    graph.add_conditional_edges("activity_specialist", should_continue)
    graph.add_conditional_edges("dining_specialist", should_continue)
    graph.add_conditional_edges("plan_synthesizer", should_continue)
    return graph


class TravelPlannerAgent:
    """LangGraph wrapper exposing a synchronous run API for travel planning."""

    def __init__(self, tracer: Optional[AzureAIOpenTelemetryTracer] = None) -> None:
        global TRACER

        # Validate mandatory configuration early.
        _google_api_key()

        self._tracer = tracer if tracer is not None else _configure_tracer()
        TRACER = self._tracer
        self._workflow = build_workflow()
        self._graph = self._workflow.compile()

    def _build_config(self, session_id: str) -> Dict[str, Any]:
        config: Dict[str, Any] = {
            "configurable": {"thread_id": session_id},
            "metadata": {
                "session_id": session_id,
                "thread_id": session_id,
            },
            "recursion_limit": 10,
        }
        if self._tracer:
            config["callbacks"] = [self._tracer]
        return config

    def run(self, prompt: str) -> str:
        """Execute the travel planner synchronously and return the itinerary."""
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
            "dining_summary": None,
            "final_itinerary": None,
            "current_agent": "start",
        }

        config = self._build_config(session_id)

        try:
            final_state = self._graph.invoke(initial_state, config=config)
            return final_state.get("final_itinerary") or ""
        except AgentConfigurationError:
            raise
        except Exception as exc:  # pragma: no cover - runtime errors surfaced
            logger.exception("Travel planner invocation failed: %s", exc)
            raise


__all__ = ["AgentConfigurationError", "TravelPlannerAgent"]
