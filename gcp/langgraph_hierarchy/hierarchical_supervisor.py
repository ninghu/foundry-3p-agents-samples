"""Hierarchical multi-agent LangGraph sample instrumented with Azure OTEL tracer.

This module demonstrates a supervisor agent delegating work to nested LangGraph
subgraphs.  It mirrors the Azure sample but routes LLM traffic to Google Gemini
so it fits the GCP sample collection.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Annotated, Any, Dict, List, Optional, Sequence, TypedDict
from uuid import uuid4
from urllib.parse import urlparse

from azure.monitor.opentelemetry import configure_azure_monitor
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import AnyMessage, add_messages

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError as exc:  # pragma: no cover - optional dependency guard
    raise ImportError(
        "Install langchain-google-genai to run the GCP hierarchical sample."
    ) from exc

try:
    from langchain_azure_ai.callbacks.tracers import AzureAIOpenTelemetryTracer
except ImportError as exc:  # pragma: no cover - optional dependency guard
    raise ImportError(
        "Install langchain-azure-ai[opentelemetry] to run the GCP hierarchical sample."
    ) from exc

load_dotenv()


# ---------------------------------------------------------------------------
# Debug callback handler to inspect LangGraph run tree
# ---------------------------------------------------------------------------


class DebugSpanPrinter(BaseCallbackHandler):
    """Print every callback event with basic hierarchy information."""

    def _meta(self, **kwargs: Any) -> str:
        meta = kwargs.get("metadata") or {}
        agent = meta.get("agent_name") or meta.get("langgraph_node")
        tool = meta.get("tool_name")
        parts: List[str] = []
        if agent:
            parts.append(f"agent={agent}")
        if tool:
            parts.append(f"tool={tool}")
        return " ".join(parts)

    def _print(self, event: str, **kwargs: Any) -> None:
        run_id = kwargs.get("run_id")
        parent_run_id = kwargs.get("parent_run_id")
        meta = self._meta(**kwargs)
        print(f"[DEBUG] {event:15s} run={run_id} parent={parent_run_id} {meta}")

    # Chain events ---------------------------------------------------------
    def on_chain_start(self, serialized, inputs, **kwargs):
        self._print("on_chain_start", **kwargs)

    def on_chain_end(self, outputs, **kwargs):
        self._print("on_chain_end", **kwargs)

    def on_chain_error(self, error, **kwargs):
        self._print("on_chain_error", **kwargs)

    # LLM events -----------------------------------------------------------
    def on_chat_model_start(self, serialized, messages, **kwargs):
        self._print("on_chat_model_start", **kwargs)

    def on_llm_end(self, response, **kwargs):
        self._print("on_llm_end", **kwargs)

    def on_llm_error(self, error, **kwargs):
        self._print("on_llm_error", **kwargs)

    # Tool events ----------------------------------------------------------
    def on_tool_start(self, serialized, input_str, **kwargs):
        self._print("on_tool_start", **kwargs)

    def on_tool_end(self, output, **kwargs):
        self._print("on_tool_end", **kwargs)

    def on_tool_error(self, error, **kwargs):
        self._print("on_tool_error", **kwargs)

    # Callback completion --------------------------------------------------
    def on_callback_end(self, *args, **kwargs):
        self._print("on_callback_end", **kwargs)


# ---------------------------------------------------------------------------
# Synthetic API tools (stand-ins for real APIs)
# ---------------------------------------------------------------------------


@tool
def create_calendar_event(
    title: str,
    start_time: str,
    end_time: str,
    attendees: list[str],
    location: str = "",
) -> str:
    """Create a calendar event from normalized inputs."""
    return (
        f"Created '{title}' from {start_time} to {end_time} "
        f"with {len(attendees)} attendee(s) at {location or 'no location'}."
    )


@tool
def get_available_time_slots(
    attendees: list[str],
    date: str,
    duration_minutes: int,
) -> list[str]:
    """Return mock available time slots for attendees on a date."""
    return ["09:00", "14:00", "16:00"]


@tool
def send_email(to: list[str], subject: str, body: str, cc: list[str] | None = None) -> str:
    """Send an email via a synthetic email API."""
    cc_part = f" (cc: {', '.join(cc)})" if cc else ""
    return f"Email sent to {', '.join(to)}{cc_part}\nSubject: {subject}\nBody: {body}"


# ---------------------------------------------------------------------------
# Helper functions for tracing metadata
# ---------------------------------------------------------------------------


def _service_name() -> str:
    return os.getenv("OTEL_SERVICE_NAME", "gcp-hierarchical-langgraph")


def _provider_name() -> str:
    return os.getenv("OTEL_GENAI_PROVIDER", "google")


def _model_name() -> str:
    return os.getenv("GOOGLE_MODEL_NAME", "gemini-2.0-flash")


def _resolve_server_attributes() -> tuple[str, int]:
    base_url = os.getenv("GOOGLE_API_BASE", "https://generativelanguage.googleapis.com")
    normalized = base_url if "://" in base_url else f"https://{base_url}"
    parsed = urlparse(normalized)
    server_address = parsed.hostname or normalized.replace("https://", "").rstrip("/")
    if parsed.port:
        return server_address, parsed.port
    return server_address, 80 if parsed.scheme == "http" else 443


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


def _graph_config(session_id: str) -> dict[str, Any]:
    callbacks = []
    if CONTEXT:
        callbacks = [cb for cb in (CONTEXT.tracer, CONTEXT.debug_handler) if cb]
    config: dict[str, Any] = {"configurable": {"thread_id": session_id}}
    if callbacks:
        config["callbacks"] = callbacks
    return config


# ---------------------------------------------------------------------------
# Dataclasses for storing agent configuration
# ---------------------------------------------------------------------------


OptionalTracer = Optional[AzureAIOpenTelemetryTracer]


@dataclass
class AgentContext:
    tracer: OptionalTracer
    debug_handler: Optional[BaseCallbackHandler]
    model_name: str


# ---------------------------------------------------------------------------
# Build LangChain agents backed by Gemini
# ---------------------------------------------------------------------------


def _google_api_key() -> str:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Set GOOGLE_API_KEY in your environment or .env file.")
    return api_key


def _build_chat_model(*, temperature: float) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=_model_name(),
        google_api_key=_google_api_key(),
        temperature=temperature,
        convert_system_message_to_user=True,
    )


def _build_agents(ctx: AgentContext) -> dict[str, Any]:
    model = _build_chat_model(temperature=0.2)

    calendar_agent = create_agent(
        model,
        tools=[create_calendar_event, get_available_time_slots],
        system_prompt=(
            "You are a calendar scheduling assistant. Always convert requests into explicit "
            "ISO datetime values. Use get_available_time_slots before creating events."
        ),
    )

    email_agent = create_agent(
        model,
        tools=[send_email],
        system_prompt=(
            "You are an email assistant. Compose professional summaries and send email updates "
            "using the send_email tool."
        ),
    )

    note_agent = create_agent(
        model,
        tools=[],
        system_prompt=(
            "You refine planning notes into conversational updates. Keep replies concise and clear."
        ),
    )

    supervisor_agent = create_agent(
        model,
        tools=[],
        system_prompt=(
            "You are a senior assistant coordinating scheduling and email follow-ups. "
            "Delegate scheduling to helpers and summarise outcomes for the user."
        ),
    )

    return {
        "calendar_agent": calendar_agent,
        "email_agent": email_agent,
        "note_agent": note_agent,
        "supervisor_agent": supervisor_agent,
    }


# ---------------------------------------------------------------------------
# Grandchild graph: note refinement agent
# ---------------------------------------------------------------------------


class GrandchildState(TypedDict):
    session_id: str
    micro_conversation: Annotated[List[AnyMessage], add_messages]


def grandchild_agent_node(
    state: GrandchildState,
    *,
    ctx: AgentContext,
    note_agent,
) -> GrandchildState:
    metadata = _agent_metadata(
        "note_agent",
        session_id=state["session_id"],
        temperature=0.3,
        agent_description="Note refinement agent",
    )
    config = {"metadata": metadata}
    callbacks = [cb for cb in (ctx.tracer, ctx.debug_handler) if cb]
    if callbacks:
        config["callbacks"] = callbacks
    result = note_agent.invoke({"messages": list(state["micro_conversation"])}, config=config)
    return {"micro_conversation": result["messages"]}


def build_grandchild_graph(ctx: AgentContext, note_agent) -> Any:
    builder = StateGraph(GrandchildState, name="grandchild_note_refiner")
    builder.add_node(
        "grandchild_agent",
        lambda state: grandchild_agent_node(state, ctx=ctx, note_agent=note_agent),
    )
    builder.add_edge(START, "grandchild_agent")
    builder.add_edge("grandchild_agent", END)
    return builder.compile()


# ---------------------------------------------------------------------------
# Child graph: scheduling flow that calls grandchild graph
# ---------------------------------------------------------------------------


class ChildState(TypedDict):
    session_id: str
    schedule_request: str
    sub_messages: Annotated[List[AnyMessage], add_messages]
    calendar_details: Optional[str]
    schedule_response: Optional[str]


def child_calendar_node(
    state: ChildState,
    *,
    ctx: AgentContext,
    calendar_agent,
) -> ChildState:
    metadata = _agent_metadata(
        "calendar_agent",
        session_id=state["session_id"],
        temperature=0.2,
        agent_description="Calendar scheduling sub-agent",
    )
    config = {"metadata": metadata}
    callbacks = [cb for cb in (ctx.tracer, ctx.debug_handler) if cb]
    if callbacks:
        config["callbacks"] = callbacks

    prompt = f"Please schedule this request: {state['schedule_request']}"
    result = calendar_agent.invoke({"messages": [HumanMessage(content=prompt)]}, config=config)
    messages = result["messages"]
    final_message = messages[-1]
    summary = final_message.content if isinstance(final_message, BaseMessage) else str(final_message)
    return {
        "sub_messages": messages,
        "calendar_details": summary,
    }


def child_followup_node(
    state: ChildState,
    *,
    grandchild_graph,
) -> ChildState:
    summary = state.get("calendar_details") or "No calendar output"
    payload = {
        "session_id": state["session_id"],
        "micro_conversation": [
            HumanMessage(
                content=(
                    "Create a concise update for stakeholders summarizing the scheduled meeting.\n"
                    f"Scheduling request: {state['schedule_request']}\n"
                    f"Calendar summary: {summary}"
                )
            )
        ],
    }
    result = grandchild_graph.invoke(payload, config=_graph_config(state["session_id"]))
    reply = result["micro_conversation"][-1]
    refined = reply.content if isinstance(reply, BaseMessage) else str(reply)
    return {"schedule_response": refined}


def build_child_graph(ctx: AgentContext, calendar_agent, grandchild_graph) -> Any:
    builder = StateGraph(ChildState, name="child_calendar_flow")
    builder.add_node(
        "calendar_planner",
        lambda state: child_calendar_node(state, ctx=ctx, calendar_agent=calendar_agent),
    )
    builder.add_node(
        "email_followup",
        lambda state: child_followup_node(state, grandchild_graph=grandchild_graph),
    )
    builder.add_edge(START, "calendar_planner")
    builder.add_edge("calendar_planner", "email_followup")
    builder.add_edge("email_followup", END)
    return builder.compile()


# ---------------------------------------------------------------------------
# Supervisor tools that call subgraphs / sub-agents
# ---------------------------------------------------------------------------


CURRENT_SESSION_ID = ""
CONTEXT: Optional[AgentContext] = None
AGENTS: dict[str, Any] = {}
CHILD_GRAPH = None


def _schedule_child_flow(request: str) -> str:
    child_input = {
        "session_id": CURRENT_SESSION_ID,
        "schedule_request": request,
        "sub_messages": [],
        "calendar_details": None,
        "schedule_response": None,
    }
    result = CHILD_GRAPH.invoke(child_input, config=_graph_config(CURRENT_SESSION_ID))
    return result.get("schedule_response") or "No schedule produced."


@tool
def schedule_event(request: str) -> str:
    """Delegate scheduling requests to the calendar workflow."""
    return _schedule_child_flow(request)


@tool
def manage_email(request: str) -> str:
    """Send follow-up emails via the email agent."""
    metadata = _agent_metadata(
        "email_agent",
        session_id=CURRENT_SESSION_ID,
        temperature=0.2,
        agent_description="Email composition agent",
    )
    config: Dict[str, Any] = {"metadata": metadata}
    callbacks = [cb for cb in (CONTEXT.tracer, CONTEXT.debug_handler) if cb]
    if callbacks:
        config["callbacks"] = callbacks
    result = AGENTS["email_agent"].invoke(
        {"messages": [HumanMessage(content=request)]},
        config=config,
    )
    message = result["messages"][-1]
    return message.content if isinstance(message, BaseMessage) else str(message)


# ---------------------------------------------------------------------------
# Parent graph using supervisor agent
# ---------------------------------------------------------------------------


class ParentState(TypedDict):
    session_id: str
    messages: Annotated[List[AnyMessage], add_messages]


def supervisor_node(state: ParentState) -> ParentState:
    global CURRENT_SESSION_ID
    CURRENT_SESSION_ID = state["session_id"]
    metadata = _agent_metadata(
        "supervisor_agent",
        session_id=state["session_id"],
        temperature=0.3,
        agent_description="Supervisor agent coordinating tasks",
    )
    config: Dict[str, Any] = {"metadata": metadata}
    callbacks = [cb for cb in (CONTEXT.tracer, CONTEXT.debug_handler) if cb]
    if callbacks:
        config["callbacks"] = callbacks
    result = AGENTS["supervisor_agent"].invoke({"messages": list(state["messages"])}, config=config)
    return {"messages": result["messages"]}


def build_parent_graph() -> Any:
    builder = StateGraph(ParentState, name="multi_agent_supervisor_parent")
    builder.add_node("supervisor", supervisor_node)
    builder.add_edge(START, "supervisor")
    builder.add_edge("supervisor", END)
    return builder.compile()


# ---------------------------------------------------------------------------
# Tracing + execution
# ---------------------------------------------------------------------------


def _configure_tracing() -> AzureAIOpenTelemetryTracer | None:
    connection_string = os.getenv("APPLICATION_INSIGHTS_CONNECTION_STRING")
    if connection_string:
        configure_azure_monitor(connection_string=connection_string)
        return AzureAIOpenTelemetryTracer(
            connection_string=connection_string,
            enable_content_recording=os.getenv(
                "APPLICATION_INSIGHTS_ENABLE_CONTENT", "true"
            ).lower()
            in {"true", "1", "yes", "on"},
            name=os.getenv("APPLICATION_INSIGHTS_AGENT_NAME", "hierarchical-supervisor"),
            agent_id=os.getenv("APPLICATION_INSIGHTS_AGENT_ID", "hierarchical-supervisor"),
            provider_name=_provider_name(),
        )
    print("APPLICATION_INSIGHTS_CONNECTION_STRING not set; running without tracing.")
    return None


def main() -> None:
    global CONTEXT, AGENTS, CHILD_GRAPH

    tracer = _configure_tracing()
    debug_handler = DebugSpanPrinter()
    CONTEXT = AgentContext(
        tracer=tracer,
        debug_handler=debug_handler,
        model_name=_model_name(),
    )

    AGENTS = _build_agents(CONTEXT)
    AGENTS["supervisor_agent"] = create_agent(
        _build_chat_model(temperature=0.2),
        tools=[schedule_event, manage_email],
        system_prompt=(
            "You are a senior assistant coordinating meetings and email follow-ups. "
            "Use the available tools to schedule events and send summaries."
        ),
    )

    grandchild_graph = build_grandchild_graph(CONTEXT, AGENTS["note_agent"])
    CHILD_GRAPH = build_child_graph(CONTEXT, AGENTS["calendar_agent"], grandchild_graph)
    parent_graph = build_parent_graph()

    scenarios_env = os.getenv("HIERARCHICAL_SCENARIOS")
    if scenarios_env:
        scenarios = [line.strip() for line in scenarios_env.splitlines() if line.strip()]
    else:
        scenarios = []

    if not scenarios:
        scenarios = [
        "Schedule a design review with the UI team next Tuesday at 2pm for an hour, "
        "and email them a reminder to review the latest mockups.",
        "Book a 30 minute sync with the data science team tomorrow at 10am and send "
        "them an email summarizing the latest experiment results.",
        "Arrange a product demo for Friday at 4pm with the sales leads and notify them "
        "to bring any questions about pricing changes.",
    ]

    print("🕸️ GCP Multi-Agent Hierarchy Sample")
    print("=" * 70)

    for idx, user_request in enumerate(scenarios, start=1):
        session_id = str(uuid4())
        CURRENT_SESSION_ID = session_id  # global update for tools
        initial_state: ParentState = {
            "session_id": session_id,
            "messages": [HumanMessage(content=user_request)],
        }

        print(f"\n=== Scenario {idx} ===")
        print(f"User Request: {user_request}\n")

        stream_config = _graph_config(session_id)
        stream_config["recursion_limit"] = 10

        for chunk in parent_graph.stream(
            initial_state,
            subgraphs=True,
            config=stream_config,
        ):
            iterable = chunk.items() if isinstance(chunk, dict) else chunk
            for entry in iterable:
                if isinstance(entry, tuple) and len(entry) == 2:
                    graph_key, step_state = entry
                else:  # pragma: no cover - defensive
                    continue
                if "messages" in step_state and step_state["messages"]:
                    message = step_state["messages"][-1]
                    if isinstance(message, BaseMessage):
                        preview = message.content
                        if len(preview) > 400:
                            preview = preview[:400] + "... [truncated]"
                        print(f"[{graph_key}] {message.type.upper()} → {preview}")

        final_state = parent_graph.invoke(
            initial_state,
            config=dict(stream_config),
        )
        final_message = final_state["messages"][-1]
        print("\nFINAL RESPONSE\n--------------")
        print(final_message.content if isinstance(final_message, BaseMessage) else str(final_message))
        print("-" * 70)


if __name__ == "__main__":
    main()
