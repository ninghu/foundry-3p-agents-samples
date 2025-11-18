"""
Currency exchange agent template for Azure Container Apps using Microsoft Agent Framework.

This sample mirrors the AWS and GCP examples while highlighting how to:
- Authenticate with Azure using Managed Identity / DefaultAzureCredential.
- Host a Microsoft Agent Framework chat agent inside a FastAPI app.
- Send OpenTelemetry traces to Azure Application Insights.
- Expose an HTTP endpoint that Azure Container Apps can scale automatically.
"""

from __future__ import annotations

import logging
import os
import time
from functools import lru_cache
from typing import Annotated, Any, Dict, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    InternalError,
    UnsupportedOperationError,
)
from a2a.utils import new_agent_text_message
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, DEFAULT_RPC_URL
from a2a.utils.error_handlers import ServerError
from agent_framework import ChatAgent
from agent_framework.azure import AzureAIAgentClient
from agent_framework.observability import setup_observability
from azure.identity.aio import DefaultAzureCredential
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry.sdk.resources import Resource

logger = logging.getLogger("azure_agent")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

SYSTEM_PROMPT = "You help users understand currency exchange rates and related context."
AGENT_NAME = os.getenv("AZURE_AGENT_NAME", "aca-currency-exchange-agent")
SERVICE_NAME = os.getenv("ACA_SERVICE_NAME", AGENT_NAME)
APPLICATION_INSIGHTS_CONNECTION_STRING = os.getenv("APPLICATION_INSIGHTS_CONNECTION_STRING")
AZURE_AI_PROJECT_ENDPOINT = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
AZURE_AI_MODEL_DEPLOYMENT_NAME = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
AZURE_AI_AGENT_ID = os.getenv("AZURE_AI_AGENT_ID")
A2A_PUBLIC_BASE_URL = os.getenv("A2A_PUBLIC_BASE_URL") or os.getenv("PUBLIC_BASE_URL")
A2A_MOUNT_PATH_DEFAULT = "/a2a"
A2A_AGENT_VERSION = os.getenv("A2A_AGENT_VERSION", "1.0.0")
A2A_AGENT_CARD_PATH = os.getenv("A2A_AGENT_CARD_PATH")
A2A_RPC_ROUTE = os.getenv("A2A_RPC_ROUTE")
A2A_AGENT_DOCUMENTATION_URL = os.getenv("A2A_AGENT_DOCUMENTATION_URL")
DEFAULT_TIMEOUT_SECONDS = 10
MAX_RETRIES = 3


def _configure_observability() -> None:
    try:
        configure_kwargs: Dict[str, Any] = {
            "resource": Resource.create({"service.name": SERVICE_NAME})
        }
        if APPLICATION_INSIGHTS_CONNECTION_STRING:
            configure_kwargs["connection_string"] = APPLICATION_INSIGHTS_CONNECTION_STRING
        else:
            logger.warning("No Application Insights connection string provided; Azure Monitor exporter disabled.")

        configure_azure_monitor(**configure_kwargs)
        setup_observability(
            enable_sensitive_data=False,
        )
        logger.info("Microsoft Agent Framework observability configured for service %s", SERVICE_NAME)
    except Exception as exc:  # pragma: no cover - best-effort telemetry setup
        logger.exception("Failed to configure observability: %s", exc)


_configure_observability()


def _normalize_path(path: Optional[str], *, default: str, allow_root: bool = False) -> str:
    value = path or default
    if not value:
        return default
    if value != "/" and not value.startswith("/"):
        value = f"/{value}"
    if value != "/" and value.endswith("/"):
        value = value.rstrip("/")
    if value == "/" and not allow_root:
        return default
    return value


A2A_MOUNT_PATH = _normalize_path(os.getenv("A2A_MOUNT_PATH"), default=A2A_MOUNT_PATH_DEFAULT)
A2A_AGENT_CARD_PATH = _normalize_path(
    A2A_AGENT_CARD_PATH,
    default=AGENT_CARD_WELL_KNOWN_PATH,
)
A2A_RPC_ROUTE = _normalize_path(
    A2A_RPC_ROUTE,
    default=DEFAULT_RPC_URL,
    allow_root=True,
)


def _resolve_public_base_url() -> str:
    base_url = os.getenv("A2A_PUBLIC_BASE_URL") or os.getenv("PUBLIC_BASE_URL") or A2A_PUBLIC_BASE_URL
    if base_url:
        return base_url.rstrip("/") + A2A_MOUNT_PATH
    port = os.getenv("PORT", "8080")
    return f"http://localhost:{port}{A2A_MOUNT_PATH}"


@lru_cache(maxsize=1)
def _build_agent_skill() -> AgentSkill:
    return AgentSkill(
        id="currency_exchange",
        name="Currency Exchange",
        description="Answers questions about currency conversion and market rates.",
        tags=["finance", "currency", "exchange"],
        examples=[
            "Convert 100 USD to EUR",
            "What is today's USD to JPY exchange rate?",
            "How has GBP trended against CAD this week?",
        ],
        inputModes=["text"],
        outputModes=["text"],
    )


@lru_cache(maxsize=1)
def _build_agent_card() -> AgentCard:
    return AgentCard(
        name=AGENT_NAME,
        description=SYSTEM_PROMPT,
        url=_resolve_public_base_url(),
        version=A2A_AGENT_VERSION,
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=False, stateTransitionHistory=False),
        skills=[_build_agent_skill()],
        documentationUrl=A2A_AGENT_DOCUMENTATION_URL,
    )


@lru_cache(maxsize=1)
def _get_task_store() -> InMemoryTaskStore:
    return InMemoryTaskStore()


class MafAgentExecutor(AgentExecutor):
    def __init__(self, agent_runtime: "AgentRuntime") -> None:
        self._runtime = agent_runtime

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        raw_input = context.get_user_input(delimiter="\n").strip()
        if not raw_input:
            logger.info("A2A received an empty prompt; returning guidance message.")
            response_text = (
                "I didn't receive any text to process. Please ask a question about currency exchange."
            )
        else:
            try:
                response_text = await self._runtime.run(raw_input)
            except Exception as exc:  # pragma: no cover - propagate to client as protocol error
                logger.exception("Microsoft Agent Framework execution failed via A2A")
                raise ServerError(InternalError(message="Agent execution failed")) from exc

        message = new_agent_text_message(
            response_text,
            context_id=context.context_id,
            task_id=context.task_id,
        )
        await event_queue.enqueue_event(message)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:  # noqa: ARG002
        logger.warning("Cancel requested for task %s but not supported.", context.task_id)
        raise ServerError(
            UnsupportedOperationError(message="Cancel is not supported for this agent.")
        )


@lru_cache(maxsize=1)
def _build_agent_executor() -> MafAgentExecutor:
    return MafAgentExecutor(runtime)


@lru_cache(maxsize=1)
def _build_a2a_application() -> A2AStarletteApplication:
    request_handler = DefaultRequestHandler(
        agent_executor=_build_agent_executor(),
        task_store=_get_task_store(),
    )
    
    def _card_modifier(card: AgentCard) -> AgentCard:
        resolved_url = _resolve_public_base_url()
        if card.url == resolved_url:
            return card
        return card.model_copy(update={"url": resolved_url})

    return A2AStarletteApplication(
        agent_card=_build_agent_card(),
        http_handler=request_handler,
        card_modifier=_card_modifier,
    )


def get_exchange_rate(
    currency_from: Annotated[str, Field(description="Base currency (3-letter ISO code).", max_length=3)] = "USD",
    currency_to: Annotated[str, Field(description="Target currency (3-letter ISO code).", max_length=3)] = "EUR",
    currency_date: Annotated[str, Field(description="Date to query (YYYY-MM-DD or 'latest').", max_length=10)] = "latest",
) -> str:
    """Retrieve the exchange rate between two currencies on a specific date using Frankfurter API."""
    endpoint = f"https://api.frankfurter.app/{currency_date}"
    params = {"base": currency_from.upper(), "symbols": currency_to.upper()}

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(endpoint, params=params, timeout=DEFAULT_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload: Dict[str, Any] = response.json()
            rate = payload["rates"][params["symbols"]]
            date = payload.get("date", currency_date)
            return f"1 {params['base']} = {rate} {params['symbols']} (as of {date})."
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            last_error = exc
            logger.warning("Attempt %s to fetch exchange rate failed: %s", attempt, exc)
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
    raise RuntimeError("Failed to retrieve exchange rate data") from last_error


class PromptRequest(BaseModel):
    prompt: str = Field(..., max_length=2048)


class AgentRuntime:
    """Lifecycle manager for Microsoft Agent Framework agent."""

    def __init__(self) -> None:
        self.credential: Optional[DefaultAzureCredential] = None
        self.client: Optional[AzureAIAgentClient] = None
        self.agent_cm: Optional[Any] = None
        self.agent: Optional[Any] = None

    async def startup(self) -> None:
        logger.info("Starting Azure agent runtime")
        has_project = bool(AZURE_AI_PROJECT_ENDPOINT)
        uses_existing_agent = has_project and bool(AZURE_AI_AGENT_ID)
        can_create_ephemeral = has_project and bool(AZURE_AI_MODEL_DEPLOYMENT_NAME)

        if not (uses_existing_agent or can_create_ephemeral):
            logger.warning(
                "Azure AI configuration not provided; starting in local fallback mode. "
                "Set AZURE_AI_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME (or AZURE_AI_AGENT_ID) "
                "to enable Microsoft Agent Framework responses."
            )

            class _FallbackAgent:
                async def run(self, prompt: str, **_: Any) -> str:  # pragma: no cover - simple demo stub
                    return (
                        "Azure AI configuration is not set for this deployment.\n"
                        "Provide AZURE_AI_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME (or AZURE_AI_AGENT_ID) "
                        "through your azd environment to activate the Microsoft Agent Framework integration.\n"
                        f"You asked: {prompt}"
                    )

            self.agent = _FallbackAgent()
            logger.info("Fallback agent ready; responding with guidance messages only.")
            return

        self.credential = DefaultAzureCredential(exclude_shared_token_cache_credential=True)
        await self.credential.__aenter__()

        client_kwargs: Dict[str, Any] = {
            "async_credential": self.credential,
            "project_endpoint": AZURE_AI_PROJECT_ENDPOINT,
        }
        if uses_existing_agent:
            client_kwargs["agent_id"] = AZURE_AI_AGENT_ID
        else:
            client_kwargs["model_deployment_name"] = AZURE_AI_MODEL_DEPLOYMENT_NAME

        self.client = AzureAIAgentClient(**client_kwargs)

        if uses_existing_agent:
            logger.info("Using existing Azure AI agent ID %s", AZURE_AI_AGENT_ID)
            self.agent_cm = ChatAgent(
                chat_client=self.client,
                instructions=SYSTEM_PROMPT,
                tools=[get_exchange_rate],
            )
        else:
            logger.info("Creating ephemeral Azure AI agent using deployment %s", AZURE_AI_MODEL_DEPLOYMENT_NAME)
            self.agent_cm = self.client.create_agent(
                name=AGENT_NAME,
                instructions=SYSTEM_PROMPT,
                tools=[get_exchange_rate],
            )

        self.agent = await self.agent_cm.__aenter__()
        logger.info("Agent runtime ready")

    async def shutdown(self) -> None:
        logger.info("Shutting down Azure agent runtime")
        if self.agent_cm:
            await self.agent_cm.__aexit__(None, None, None)
        if self.credential:
            await self.credential.__aexit__(None, None, None)
        logger.info("Agent runtime shutdown complete")

    async def run(self, prompt: str) -> str:
        if not self.agent:
            raise RuntimeError("Agent runtime is not initialized")

        # Maintain stateless interactions by creating a dedicated conversation thread per request.
        run_kwargs: Dict[str, Any] = {}
        thread_factory = getattr(self.agent, "get_new_thread", None)
        if callable(thread_factory):
            run_kwargs["thread"] = thread_factory()

        result = await self.agent.run(prompt, **run_kwargs)

        if hasattr(result, "text"):
            return str(result.text)
        if isinstance(result, dict) and "result" in result:
            return str(result["result"])
        return str(result)


runtime = AgentRuntime()
app = FastAPI(title="Azure Container Apps - Microsoft Agent Framework Sample", version="0.1.0")

try:
    a2a_application = _build_a2a_application()
    a2a_asgi_app = a2a_application.build(
        agent_card_url=A2A_AGENT_CARD_PATH,
        rpc_url=A2A_RPC_ROUTE,
    )
    app.mount(
        A2A_MOUNT_PATH,
        a2a_asgi_app,
    )
    logger.info(
        "A2A endpoints mounted at %s (card path: %s, rpc path: %s)",
        A2A_MOUNT_PATH,
        A2A_AGENT_CARD_PATH,
        A2A_RPC_ROUTE,
    )

    def _make_redirect(relative_path: str) -> RedirectResponse:
        target = relative_path if A2A_MOUNT_PATH == "/" else f"{A2A_MOUNT_PATH}{relative_path}"
        return RedirectResponse(url=target)

    if A2A_MOUNT_PATH != "/":

        @app.get("/.well-known/agent-card.json")
        async def _root_agent_card_redirect() -> RedirectResponse:
            return _make_redirect("/.well-known/agent-card.json")

        @app.api_route("/rpc", methods=["GET", "POST"])
        async def _root_rpc_redirect() -> RedirectResponse:
            return _make_redirect(A2A_RPC_ROUTE)
except ImportError as exc:  # pragma: no cover - optional dependency guard
    logger.warning("A2A HTTP server dependencies missing: %s", exc)


@app.on_event("startup")
async def on_startup() -> None:
    await runtime.startup()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await runtime.shutdown()


@app.get("/healthz")
async def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/invoke")
async def invoke(request: PromptRequest) -> Dict[str, str]:
    try:
        answer = await runtime.run(request.prompt)
    except Exception as exc:  # pragma: no cover - surface to HTTP caller
        logger.exception("Agent invocation failed")
        raise HTTPException(status_code=500, detail=f"Agent invocation failed: {exc}") from exc
    return {"result": answer}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "agent_framework_container_app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        reload=bool(os.getenv("RELOAD", "")),
    )
