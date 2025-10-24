"""FastAPI entrypoint for the Cloud Run exchange rate agent."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Optional

import click
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

try:
    from .agent import AgentConfigurationError, ExchangeRateAgent
    from .tracing import configure_azure_tracer
except ImportError:  # Fallback when running as a script
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.append(str(project_root))

    from gcp.cloud_run_agent.agent import AgentConfigurationError, ExchangeRateAgent
    from gcp.cloud_run_agent.tracing import configure_azure_tracer


load_dotenv()


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class QueryRequest(BaseModel):
    """Inbound request payload."""

    prompt: str = Field(..., description="User question about currency exchange rates.")


class QueryResponse(BaseModel):
    """Outbound response payload."""

    result: str = Field(..., description="Agent-formatted answer.")


app = FastAPI(
    title="Cloud Run Exchange Rate Agent",
    version="1.0.0",
    description="LangGraph-based currency exchange agent instrumented with Azure Application Insights.",
)

_agent: Optional[ExchangeRateAgent] = None
_agent_error: Optional[Exception] = None


def _ensure_agent() -> ExchangeRateAgent:
    global _agent, _agent_error  # noqa: PLW0603 - module-level cache
    if _agent is not None:
        return _agent
    if _agent_error is not None:
        raise _agent_error

    try:
        tracer = configure_azure_tracer()
        _agent = ExchangeRateAgent(tracer=tracer)
        return _agent
    except AgentConfigurationError as exc:
        _agent_error = exc
        logger.error("Agent configuration error: %s", exc)
        raise
    except Exception as exc:  # pragma: no cover - surfaced at runtime
        _agent_error = exc
        logger.exception("Unexpected failure while creating the agent: %s", exc)
        raise


@app.get("/healthz", tags=["utility"])
async def healthcheck() -> dict[str, str]:
    """Basic health probe for Cloud Run."""
    try:
        _ensure_agent()
    except Exception as exc:  # pragma: no cover - runtime path
        logger.error("Health check failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    return {"status": "ok"}


@app.post("/invoke", response_model=QueryResponse, tags=["exchange"])
async def run_exchange(request: QueryRequest) -> QueryResponse:
    """Run the exchange rate agent against an incoming prompt."""
    agent = _ensure_agent()
    try:
        result = await asyncio.to_thread(agent.run, request.prompt)
    except AgentConfigurationError as exc:
        logger.error("Agent misconfiguration detected: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - runtime path
        logger.exception("Agent execution failed: %s", exc)
        raise HTTPException(status_code=500, detail="Agent execution failed.") from exc

    return QueryResponse(result=result)


@app.get("/", tags=["exchange"])
async def index() -> dict[str, str]:
    """Human-friendly landing endpoint."""
    return {
        "message": "Post a prompt to /invoke to ask about currency conversions.",
    }


@click.command()
@click.option("--host", "host", default=None)
@click.option("--port", "port", type=int, default=None)
def main(host: str | None, port: int | None) -> None:
    """Starts the Currency Agent server."""
    effective_host = host or os.getenv("BIND_HOST") or os.getenv("HOST", "0.0.0.0")
    effective_port = port or int(os.getenv("PORT", "8080"))

    try:
        _ensure_agent()
    except Exception as exc:  # noqa: BLE001 - propagate startup failures
        logger.error("Agent initialization failed: %s", exc)
        sys.exit(1)

    uvicorn.run(app, host=effective_host, port=effective_port)


if __name__ == "__main__":
    main()
