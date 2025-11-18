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
from typing import Annotated, Any, Dict, Optional

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

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