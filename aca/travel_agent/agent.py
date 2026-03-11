"""Multi-agent travel planner on Azure Container Apps with Foundry tracing."""

import logging
import os
import random
from datetime import datetime, timedelta
from typing import Annotated, Any, List, Optional, TypedDict
from uuid import uuid4

from dotenv import load_dotenv
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import AnyMessage, add_messages

from azure.monitor.opentelemetry import configure_azure_monitor
from langchain_azure_ai.callbacks.tracers import AzureAIOpenTelemetryTracer

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — all from environment variables
# ---------------------------------------------------------------------------

AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-52")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
APPINSIGHTS_CONN_STR = os.environ.get("APPLICATION_INSIGHTS_CONNECTION_STRING", "")
OTEL_AGENT_ID = os.environ.get("OTEL_AGENT_ID", "aca-travel-planner")

# ---------------------------------------------------------------------------
# Mock destination data
# ---------------------------------------------------------------------------

DESTINATIONS = {
    "paris": ["Eiffel Tower at sunset", "Seine dinner cruise", "Day trip to Versailles"],
    "tokyo": ["Sushi masterclass in Tsukiji", "Ghibli Museum visit", "Hakone hot springs"],
    "rome":  ["Colosseum underground tour", "Pasta masterclass", "Sunset walk in Trastevere"],
}

# ---------------------------------------------------------------------------
# LangGraph state
# ---------------------------------------------------------------------------


class PlannerState(TypedDict):
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


# ---------------------------------------------------------------------------
# Tracer (global — set once at startup)
# ---------------------------------------------------------------------------

TRACER: Optional[AzureAIOpenTelemetryTracer] = None


def _init_tracer() -> Optional[AzureAIOpenTelemetryTracer]:
    if not APPINSIGHTS_CONN_STR:
        logger.info("No APPLICATION_INSIGHTS_CONNECTION_STRING — tracing disabled.")
        return None
    configure_azure_monitor(connection_string=APPINSIGHTS_CONN_STR)
    return AzureAIOpenTelemetryTracer(
        connection_string=APPINSIGHTS_CONN_STR,
        enable_content_recording=True,
        agent_id=OTEL_AGENT_ID,
        provider_name="azure",
    )


# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------


def _llm(temperature: float = 0.3) -> ChatOpenAI:
    base_url = AZURE_OPENAI_ENDPOINT.rstrip("/") + "/openai/v1"
    return ChatOpenAI(
        model=AZURE_OPENAI_DEPLOYMENT,
        base_url=base_url,
        api_key=AZURE_OPENAI_API_KEY,
        temperature=temperature,
    )


# ---------------------------------------------------------------------------
# Tool-calling loop (replaces create_react_agent to avoid duplicate spans)
# ---------------------------------------------------------------------------


def _call_with_tools(
    llm: ChatOpenAI,
    tools: list,
    messages: list[BaseMessage],
    config: RunnableConfig,
    max_iterations: int = 5,
) -> str:
    """Invoke the LLM with tools, looping until no more tool calls."""
    llm_runnable = llm.bind_tools(tools) if tools else llm
    tool_map = {t.name: t for t in tools}

    for _ in range(max_iterations):
        response = llm_runnable.invoke(messages, config=config)
        messages.append(response)

        if not getattr(response, "tool_calls", None):
            return response.content if isinstance(response, BaseMessage) else str(response)

        for tc in response.tool_calls:
            tool_fn = tool_map.get(tc["name"])
            if tool_fn:
                result = tool_fn.invoke(tc["args"], config=config)
                messages.append(
                    ToolMessage(content=str(result), tool_call_id=tc["id"])
                )

    last = messages[-1]
    return last.content if isinstance(last, BaseMessage) else str(last)


# ---------------------------------------------------------------------------
# Mock tools
# ---------------------------------------------------------------------------


@tool
def search_flights(origin: str, destination: str, departure: str) -> str:
    """Return a synthetic flight option."""
    random.seed(hash((origin, destination, departure)) % (2**32))
    airline = random.choice(["SkyLine", "AeroJet", "CloudNine"])
    fare = random.randint(700, 1250)
    return f"{airline} non-stop {origin}->{destination}, depart {departure} 09:05, ${fare} return."


@tool
def search_hotels(destination: str, check_in: str, check_out: str) -> str:
    """Return a synthetic hotel option."""
    random.seed(hash((destination, check_in, check_out)) % (2**32))
    name = random.choice(["Maison Azure", "Le Jardin", "Vista Royale"])
    rate = random.randint(220, 380)
    return f"{name} — chic suites, rooftop lounge, ${rate}/night with breakfast."


@tool
def search_activities(destination: str) -> str:
    """Return highlight activities for the destination."""
    highlights = DESTINATIONS.get(destination.lower(), DESTINATIONS["paris"])
    return "\n".join(f"- {h}" for h in highlights)


# ---------------------------------------------------------------------------
# Specialist agent nodes
# ---------------------------------------------------------------------------


def coordinator_node(state: PlannerState, config: RunnableConfig) -> PlannerState:
    result = _call_with_tools(
        _llm(0.2), [],
        [
            SystemMessage(content="You are the lead travel coordinator. Summarise the plan."),
            *state["messages"],
        ],
        config,
    )
    state["messages"].append(AIMessage(content=result))
    state["current_agent"] = "flight_specialist"
    return state


def flight_node(state: PlannerState, config: RunnableConfig) -> PlannerState:
    task = (f"Find a flight from {state['origin']} to {state['destination']} "
            f"departing {state['departure']} for {state['travellers']} travellers.")
    result = _call_with_tools(
        _llm(0.4), [search_flights],
        [HumanMessage(content=task)],
        config,
    )
    state["flight_summary"] = result
    state["current_agent"] = "hotel_specialist"
    return state


def hotel_node(state: PlannerState, config: RunnableConfig) -> PlannerState:
    task = (f"Recommend a hotel in {state['destination']} from {state['departure']} "
            f"to {state['return_date']} for {state['travellers']} travellers.")
    result = _call_with_tools(
        _llm(0.4), [search_hotels],
        [HumanMessage(content=task)],
        config,
    )
    state["hotel_summary"] = result
    state["current_agent"] = "activity_specialist"
    return state


def activity_node(state: PlannerState, config: RunnableConfig) -> PlannerState:
    task = f"Suggest activities for a week in {state['destination']}."
    result = _call_with_tools(
        _llm(0.5), [search_activities],
        [HumanMessage(content=task)],
        config,
    )
    state["activities_summary"] = result
    state["current_agent"] = "synthesizer"
    return state


def synthesizer_node(state: PlannerState, config: RunnableConfig) -> PlannerState:
    prompt = (
        "Combine these specialist outputs into a polished travel itinerary.\n\n"
        f"Request: {state['user_request']}\n"
        f"Route: {state['origin']} -> {state['destination']}\n"
        f"Dates: {state['departure']} to {state['return_date']}\n\n"
        f"Flight: {state['flight_summary']}\n"
        f"Hotel: {state['hotel_summary']}\n"
        f"Activities: {state['activities_summary']}"
    )
    result = _call_with_tools(
        _llm(0.3), [],
        [HumanMessage(content=prompt)],
        config,
    )
    state["final_itinerary"] = result
    state["current_agent"] = "done"
    return state


# ---------------------------------------------------------------------------
# Graph wiring
# ---------------------------------------------------------------------------

def build_graph() -> StateGraph:
    g = StateGraph(PlannerState)
    g.add_node("coordinator", coordinator_node)
    g.add_node("flight_specialist", flight_node)
    g.add_node("hotel_specialist", hotel_node)
    g.add_node("activity_specialist", activity_node)
    g.add_node("synthesizer", synthesizer_node)

    g.add_edge(START, "coordinator")
    g.add_edge("coordinator", "flight_specialist")
    g.add_edge("flight_specialist", "hotel_specialist")
    g.add_edge("hotel_specialist", "activity_specialist")
    g.add_edge("activity_specialist", "synthesizer")
    g.add_edge("synthesizer", END)
    return g


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pick(text: str, options: list[str], default: str) -> str:
    low = text.lower()
    return next((o.title() for o in options if o in low), default)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class TravelPlannerAgent:
    """Thin wrapper that compiles the graph once and exposes a sync `run` method."""

    def __init__(self) -> None:
        global TRACER
        if not AZURE_OPENAI_ENDPOINT or not AZURE_OPENAI_API_KEY:
            raise RuntimeError(
                "Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY in .env"
            )
        TRACER = _init_tracer()
        self._graph = build_graph().compile(name="aca-travel-planner")

    def run(self, prompt: str) -> str:
        session_id = str(uuid4())
        dep, ret = datetime.now() + timedelta(days=21), datetime.now() + timedelta(days=26)
        state: PlannerState = {
            "messages": [HumanMessage(content=prompt)],
            "user_request": prompt,
            "session_id": session_id,
            "origin": _pick(prompt, ["seattle", "new york", "san francisco", "london"], "Seattle"),
            "destination": _pick(prompt, list(DESTINATIONS.keys()), "Paris"),
            "departure": dep.strftime("%Y-%m-%d"),
            "return_date": ret.strftime("%Y-%m-%d"),
            "travellers": 2,
            "flight_summary": None,
            "hotel_summary": None,
            "activities_summary": None,
            "final_itinerary": None,
            "current_agent": "coordinator",
        }
        config: dict[str, Any] = {
            "configurable": {"thread_id": session_id},
            "recursion_limit": 10,
            "metadata": {
                "otel_agent_span": True,
                "session_id": session_id,
                "thread_id": session_id,
                "gen_ai.provider.name": "azure",
                "gen_ai.request.model": AZURE_OPENAI_DEPLOYMENT,
                "gen_ai.conversation.id": session_id,
            },
        }
        if TRACER:
            config["callbacks"] = [TRACER]
        result = self._graph.invoke(state, config=config)
        return result.get("final_itinerary") or ""
