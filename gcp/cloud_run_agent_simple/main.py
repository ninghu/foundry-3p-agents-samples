"""FastAPI entrypoint for the Cloud Run currency agent."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Optional

import click
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

try:
    from .agent import ExchangeRateAgent
except ImportError:  # pragma: no cover - fallback for script execution
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.append(str(project_root))

    from gcp.cloud_run_agent_simple.agent import ExchangeRateAgent


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class QueryRequest(BaseModel):
    """Inbound request payload."""

    prompt: str = Field(..., description="User query requesting currency help.")


class QueryResponse(BaseModel):
    """Outbound response payload."""

    result: str = Field(..., description="Answer from the currency agent.")


app = FastAPI(
    title="Cloud Run Currency Agent",
    version="1.0.0",
    description="LangGraph-based currency assistant with optional Azure Application Insights tracing.",
)

_agent: Optional[ExchangeRateAgent] = None
_agent_error: Optional[Exception] = None

try:
    _agent = ExchangeRateAgent()
except Exception as exc:  # pragma: no cover - surfaced at runtime
    _agent_error = exc
    logger.exception("Unexpected failure while creating the agent: %s", exc)


@app.get("/healthz", tags=["utility"])
async def healthcheck() -> dict[str, str]:
    """Basic health probe for Cloud Run."""
    if _agent_error is not None:
        raise HTTPException(status_code=500, detail=str(_agent_error))
    return {"status": "ok"}


@app.post("/invoke", response_model=QueryResponse, tags=["currency"])
async def invoke_agent(request: QueryRequest, http_request: Request) -> QueryResponse:
    """Run the currency agent against an incoming prompt."""
    if _agent is None:
        detail = str(_agent_error) if _agent_error else "Agent is not available."
        raise HTTPException(status_code=500, detail=detail)

    context = {"headers": dict(http_request.headers)}

    try:
        result = await asyncio.to_thread(_agent.run, request.prompt, context)
    except Exception as exc:  # pragma: no cover - runtime path
        logger.exception("Agent execution failed: %s", exc)
        raise HTTPException(status_code=500, detail="Agent execution failed.") from exc

    return QueryResponse(result=result)


@app.get("/", tags=["currency"])
async def index() -> dict[str, str]:
    """Human-friendly landing endpoint."""
    return {
        "message": "Post a prompt to /invoke to retrieve currency exchange details.",
    }


@click.command()
@click.option("--host", "host", default=None)
@click.option("--port", "port", type=int, default=None)
def main(host: str | None, port: int | None) -> None:
    """Start the Currency Agent server."""
    effective_host = host or os.getenv("BIND_HOST") or os.getenv("HOST", "0.0.0.0")
    effective_port = port or int(os.getenv("PORT", "8080"))

    if _agent is None:
        detail = str(_agent_error) if _agent_error else "Agent is not available."
        logger.error("Agent initialization failed: %s", detail)
        sys.exit(1)

    uvicorn.run(app, host=effective_host, port=effective_port)


if __name__ == "__main__":
    main()
