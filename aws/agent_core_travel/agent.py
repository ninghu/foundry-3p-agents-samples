"""Multi-agent travel planner on AWS Bedrock AgentCore with ADOT telemetry."""

from __future__ import annotations

import logging
import os
import random
from datetime import datetime, timedelta
from typing import Annotated, Any, Dict, List, Optional
from uuid import uuid4

import boto3
from dotenv import load_dotenv
from langchain_aws.chat_models import ChatBedrock as _BedrockChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import AnyMessage, add_messages
from typing_extensions import TypedDict

from bedrock_agentcore import BedrockAgentCoreApp

# ---------------------------------------------------------------------------
# OpenTelemetry — pure OTel SDK, exports OTLP to ADOT Collector
# ---------------------------------------------------------------------------

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20240620-v1:0"
)
AGENT_NAME = os.getenv("AGENT_NAME", "aws-travel-planner-agent")
AGENT_ID = os.getenv("AGENT_ID", "aws-agentcore-travel-planner")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
APPINSIGHTS_CONN_STR = os.getenv("APPLICATION_INSIGHTS_CONNECTION_STRING", "")

# ---------------------------------------------------------------------------
# OpenTelemetry setup — dual export: OTLP→ADOT + AzureMonitor→App Insights
# ---------------------------------------------------------------------------

try:
    from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
except ImportError:
    AzureMonitorTraceExporter = None  # type: ignore
    logger.warning("azure-monitor-opentelemetry-exporter not installed; App Insights direct export disabled.")


def _init_otel() -> trace.Tracer:
    """Configure OTel with dual export: OTLP to ADOT Collector + Azure Monitor to App Insights."""
    resource = Resource.create(
        {
            "service.name": AGENT_NAME,
            "service.namespace": "foundry-agents",
            "gen_ai.agent.id": AGENT_ID,
            "gen_ai.agent.name": AGENT_NAME,
        }
    )
    provider = TracerProvider(resource=resource)

    # 1) OTLP exporter → ADOT Collector
    try:
        otlp_exporter = OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        logger.info("OTel: OTLP exporter configured → %s", OTEL_ENDPOINT)
    except Exception as exc:
        logger.warning("OTel: OTLP exporter failed to initialize: %s", exc)

    # 2) Azure Monitor exporter → Application Insights (direct)
    if AzureMonitorTraceExporter and APPINSIGHTS_CONN_STR:
        az_exporter = AzureMonitorTraceExporter(connection_string=APPINSIGHTS_CONN_STR)
        provider.add_span_processor(BatchSpanProcessor(az_exporter))
        logger.info("OTel: Azure Monitor exporter configured → App Insights")

    trace.set_tracer_provider(provider)
    return trace.get_tracer("travel-planner-agent")


tracer = _init_otel()

# ---------------------------------------------------------------------------
# Mock destination data
# ---------------------------------------------------------------------------

DESTINATIONS = {
    "paris": [
        "Eiffel Tower at sunset",
        "Seine dinner cruise",
        "Day trip to Versailles",
    ],
    "tokyo": [
        "Sushi masterclass in Tsukiji",
        "Ghibli Museum visit",
        "Hakone hot springs",
    ],
    "rome": [
        "Colosseum underground tour",
        "Pasta masterclass",
        "Sunset walk in Trastevere",
    ],
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
# LLM helper
# ---------------------------------------------------------------------------


def _llm(temperature: float = 0.3) -> _BedrockChatModel:
    client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    return _BedrockChatModel(
        client=client, model_id=BEDROCK_MODEL_ID, model_kwargs={"temperature": temperature}
    )


# ---------------------------------------------------------------------------
# Tool-calling loop (avoids duplicate spans from create_react_agent)
# ---------------------------------------------------------------------------


def _call_with_tools(
    llm: _BedrockChatModel,
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
# Specialist agent nodes — each wrapped in an OTel span
# ---------------------------------------------------------------------------


def coordinator_node(state: PlannerState, config: RunnableConfig) -> PlannerState:
    with tracer.start_as_current_span(
        "coordinator",
        attributes={
            "gen_ai.agent.name": "coordinator",
            "gen_ai.request.model": BEDROCK_MODEL_ID,
        },
    ):
        result = _call_with_tools(
            _llm(0.2),
            [],
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
    with tracer.start_as_current_span(
        "flight_specialist",
        attributes={
            "gen_ai.agent.name": "flight_specialist",
            "gen_ai.request.model": BEDROCK_MODEL_ID,
        },
    ):
        task = (
            f"Find a flight from {state['origin']} to {state['destination']} "
            f"departing {state['departure']} for {state['travellers']} travellers."
        )
        result = _call_with_tools(
            _llm(0.4), [search_flights], [HumanMessage(content=task)], config
        )
        state["flight_summary"] = result
        state["current_agent"] = "hotel_specialist"
        return state


def hotel_node(state: PlannerState, config: RunnableConfig) -> PlannerState:
    with tracer.start_as_current_span(
        "hotel_specialist",
        attributes={
            "gen_ai.agent.name": "hotel_specialist",
            "gen_ai.request.model": BEDROCK_MODEL_ID,
        },
    ):
        task = (
            f"Recommend a hotel in {state['destination']} from {state['departure']} "
            f"to {state['return_date']} for {state['travellers']} travellers."
        )
        result = _call_with_tools(
            _llm(0.4), [search_hotels], [HumanMessage(content=task)], config
        )
        state["hotel_summary"] = result
        state["current_agent"] = "activity_specialist"
        return state


def activity_node(state: PlannerState, config: RunnableConfig) -> PlannerState:
    with tracer.start_as_current_span(
        "activity_specialist",
        attributes={
            "gen_ai.agent.name": "activity_specialist",
            "gen_ai.request.model": BEDROCK_MODEL_ID,
        },
    ):
        task = f"Suggest activities for a week in {state['destination']}."
        result = _call_with_tools(
            _llm(0.5), [search_activities], [HumanMessage(content=task)], config
        )
        state["activities_summary"] = result
        state["current_agent"] = "synthesizer"
        return state


def synthesizer_node(state: PlannerState, config: RunnableConfig) -> PlannerState:
    with tracer.start_as_current_span(
        "synthesizer",
        attributes={
            "gen_ai.agent.name": "synthesizer",
            "gen_ai.request.model": BEDROCK_MODEL_ID,
        },
    ):
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
            _llm(0.3), [], [HumanMessage(content=prompt)], config
        )
        state["final_itinerary"] = result
        state["current_agent"] = "done"
        return state


# ---------------------------------------------------------------------------
# Graph wiring
# ---------------------------------------------------------------------------


def _build_graph():
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

    return g.compile(name="aws-travel-planner")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pick(text: str, options: list[str], default: str) -> str:
    low = text.lower()
    return next((o.title() for o in options if o in low), default)


def _last_message_content(messages: List[Any]) -> str:
    if not messages:
        return ""
    last = messages[-1]
    if hasattr(last, "content"):
        return str(last.content)
    if isinstance(last, dict):
        return str(last.get("content", ""))
    return str(last)


# ---------------------------------------------------------------------------
# BedrockAgentCoreApp entrypoint
# ---------------------------------------------------------------------------

app = BedrockAgentCoreApp()
compiled_graph = _build_graph()


@app.entrypoint
def invoke(payload: Dict[str, Any], context: Dict[str, Any] | None = None) -> Dict[str, str]:
    """Invoke the travel planner agent with the provided payload."""

    user_message = payload.get("prompt", "Plan a trip from Seattle to Paris")
    session_id = str(uuid4())

    dep = datetime.now() + timedelta(days=21)
    ret = datetime.now() + timedelta(days=26)

    state: PlannerState = {
        "messages": [HumanMessage(content=user_message)],
        "user_request": user_message,
        "session_id": session_id,
        "origin": _pick(user_message, ["seattle", "new york", "san francisco", "london"], "Seattle"),
        "destination": _pick(user_message, list(DESTINATIONS.keys()), "Paris"),
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
            "session_id": session_id,
            "gen_ai.agent.id": AGENT_ID,
            "gen_ai.agent.name": AGENT_NAME,
            "gen_ai.request.model": BEDROCK_MODEL_ID,
        },
    }

    with tracer.start_as_current_span(
        "travel_planner_invoke",
        attributes={
            "gen_ai.agent.id": AGENT_ID,
            "gen_ai.agent.name": AGENT_NAME,
            "gen_ai.request.model": BEDROCK_MODEL_ID,
            "session.id": session_id,
            "user.request": user_message,
        },
    ):
        try:
            result = compiled_graph.invoke(state, config=config)
            answer = result.get("final_itinerary") or _last_message_content(
                result.get("messages", [])
            )
        except Exception as exc:
            logger.exception("Error during travel planning")
            answer = f"Error while processing request: {exc}"

    return {"result": answer}


if __name__ == "__main__":
    app.run()
