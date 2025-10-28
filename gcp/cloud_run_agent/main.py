"""FastAPI entrypoint for the Cloud Run travel planner agent."""

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
    from .agent import AgentConfigurationError, TravelPlannerAgent
except ImportError:  # Fallback when running as a script
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.append(str(project_root))

    from gcp.cloud_run_agent.agent import (
        AgentConfigurationError,
        TravelPlannerAgent,
    )


load_dotenv()


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# Suppress verbose Azure SDK HTTP logging that floods the console.
for noisy_logger in (
    "azure",
    "azure.core.pipeline.policies.http_logging_policy",
    "azure.monitor.opentelemetry.exporter.export._base",
):
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)


class QueryRequest(BaseModel):
    """Inbound request payload."""

    prompt: str = Field(..., description="Traveller request describing desired plans.")


class QueryResponse(BaseModel):
    """Outbound response payload."""

    result: str = Field(..., description="Structured itinerary from the travel planner.")


app = FastAPI(
    title="Cloud Run Travel Planner Agent",
    version="1.0.0",
    description="LangGraph-based multi-agent travel planner instrumented with Azure Application Insights.",
)

_agent: Optional[TravelPlannerAgent] = None
_agent_error: Optional[Exception] = None


try:
    _agent = TravelPlannerAgent()
except AgentConfigurationError as exc:
    _agent_error = exc
    logger.error("Agent configuration error during startup: %s", exc)
except Exception as exc:  # pragma: no cover - surfaced at runtime
    _agent_error = exc
    logger.exception("Unexpected failure while creating the agent: %s", exc)


@app.get("/healthz", tags=["utility"])
async def healthcheck() -> dict[str, str]:
    """Basic health probe for Cloud Run."""
    if _agent_error is not None:
        logger.error("Health check failed: %s", _agent_error)
        raise HTTPException(status_code=500, detail=str(_agent_error))
    return {"status": "ok"}


@app.post("/invoke", response_model=QueryResponse, tags=["travel"])
async def run_planner(request: QueryRequest) -> QueryResponse:
    """Run the travel planner agent against an incoming prompt."""
    if _agent is None:
        detail = str(_agent_error) if _agent_error else "Agent is not available."
        logger.error("Agent execution failed: %s", detail)
        raise HTTPException(status_code=500, detail=detail)
    agent = _agent
    try:
        result = await asyncio.to_thread(agent.run, request.prompt)
    except AgentConfigurationError as exc:
        logger.error("Agent misconfiguration detected: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - runtime path
        logger.exception("Agent execution failed: %s", exc)
        raise HTTPException(status_code=500, detail="Agent execution failed.") from exc

    return QueryResponse(result=result)


@app.get("/", tags=["travel"])
async def index() -> dict[str, str]:
    """Human-friendly landing endpoint."""
    return {
        "message": "Post a prompt to /invoke to receive a multi-agent travel itinerary.",
    }


@click.command()
@click.option("--host", "host", default=None)
@click.option("--port", "port", type=int, default=None)
def main(host: str | None, port: int | None) -> None:
    """Starts the Travel Planner Agent server."""
    effective_host = host or os.getenv("BIND_HOST") or os.getenv("HOST", "0.0.0.0")
    effective_port = port or int(os.getenv("PORT", "8080"))

    if _agent is None:
        detail = str(_agent_error) if _agent_error else "Agent is not available."
        logger.error("Agent initialization failed: %s", detail)
        sys.exit(1)

    uvicorn.run(app, host=effective_host, port=effective_port)


if __name__ == "__main__":
    main()
