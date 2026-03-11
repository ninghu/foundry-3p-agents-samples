"""FastAPI server for the ACA travel planner agent."""

import asyncio
import logging
import os

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .agent import TravelPlannerAgent

load_dotenv()
logging.basicConfig(level=logging.INFO)
# Quiet noisy Azure SDK loggers
for name in ("azure", "azure.core.pipeline.policies.http_logging_policy"):
    logging.getLogger(name).setLevel(logging.WARNING)


class QueryRequest(BaseModel):
    prompt: str


class QueryResponse(BaseModel):
    result: str


app = FastAPI(title="ACA Travel Planner Agent")

_agent: TravelPlannerAgent | None = None
_error: Exception | None = None

try:
    _agent = TravelPlannerAgent()
except Exception as exc:
    _error = exc
    logging.getLogger(__name__).error("Agent init failed: %s", exc)


@app.get("/healthz")
async def healthz():
    if _error:
        raise HTTPException(500, detail=str(_error))
    return {"status": "ok"}


@app.post("/invoke", response_model=QueryResponse)
async def invoke(req: QueryRequest):
    if _agent is None:
        raise HTTPException(500, detail=str(_error) if _error else "Agent unavailable")
    result = await asyncio.to_thread(_agent.run, req.prompt)
    return QueryResponse(result=result)


@app.get("/")
async def index():
    return {"message": "POST a prompt to /invoke to get a travel itinerary."}


if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8080")))
