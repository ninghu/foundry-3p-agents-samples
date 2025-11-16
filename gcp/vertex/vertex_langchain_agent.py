import atexit
import logging
import os
import vertexai
from vertexai import agent_engines
from typing import Sequence
from pathlib import Path
from langchain_core.language_models import BaseLanguageModel
from langchain_core.prompts import BasePromptTemplate
from langchain_core.tools import BaseTool
from langchain_core.tools import tool
from dotenv import load_dotenv

try:
    from langchain_azure_ai.callbacks.tracers import (
        AzureAIOpenTelemetryTracer,
    )
except ImportError:  # pragma: no cover - optional dependency
    AzureAIOpenTelemetryTracer = None  # type: ignore


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
if not PROJECT:
    raise RuntimeError("Set GOOGLE_CLOUD_PROJECT in your environment or .env file.")

LOCATION = os.getenv("GOOGLE_CLOUD_REGION", "us-central1")
MODEL_NAME = os.getenv("VERTEX_MODEL_NAME", "gemini-2.0-flash")
APPLICATION_INSIGHTS_CONNECTION_STRING = os.getenv("APPLICATION_INSIGHTS_CONNECTION_STRING")
AGENT_NAME = os.getenv("VERTEX_AGENT_NAME", "gcp-currency-exchange-agent")
AGENT_ID = os.getenv("VERTEX_AGENT_ID", "gcp-agent")
PROVIDER_NAME = os.getenv("VERTEX_PROVIDER_NAME", "gcp.vertex_ai")
GCS_DIR_NAME = os.getenv("VERTEX_GCS_DIR_NAME", "dev")
STAGING_BUCKET = os.getenv("VERTEX_STAGING_BUCKET")

if APPLICATION_INSIGHTS_CONNECTION_STRING:
    logger.info("Azure Application Insights tracing enabled for Vertex agent.")
else:
    logger.info("APPLICATION_INSIGHTS_CONNECTION_STRING not provided; tracing disabled.")

vertexai.init(
    project=PROJECT,
    location=LOCATION,
)

@tool
def get_exchange_rate(
    currency_from: str = "USD",
    currency_to: str = "EUR",
    currency_date: str = "latest",
):
    """Retrieves the exchange rate between two currencies on a specified date.

    Uses the Frankfurter API (https://api.frankfurter.app/) to obtain
    exchange rate data.

    Args:
        currency_from: The base currency (3-letter currency code).
            Defaults to "USD" (US Dollar).
        currency_to: The target currency (3-letter currency code).
            Defaults to "EUR" (Euro).
        currency_date: The date for which to retrieve the exchange rate.
            Defaults to "latest" for the most recent exchange rate data.
            Can be specified in YYYY-MM-DD format for historical rates.

    Returns:
        dict: A dictionary containing the exchange rate information.
            Example: {"amount": 1.0, "base": "USD", "date": "2023-11-24",
                "rates": {"EUR": 0.95534}}
    """
    import requests
    response = requests.get(
        f"https://api.frankfurter.app/{currency_date}",
        params={"base": currency_from, "symbols": currency_to},
    )
    return response.json()
    

def custom_runnable_builder(
    model: BaseLanguageModel,
    *,
    tools: Sequence[BaseTool],
    prompt: BasePromptTemplate = None,
    agent_executor_kwargs = None,
    **kwargs,
):
    from langchain.agents import AgentExecutor, create_tool_calling_agent
    from langchain_core.tools import tool
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    
    tools = tools or []
    agent_executor_kwargs = agent_executor_kwargs or {}
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a helpful assistant for currency exchange rates."),
        ("user", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])

    agent = create_tool_calling_agent(model, tools, prompt)
    executor = AgentExecutor(
        agent=agent, 
        tools=tools,
        **agent_executor_kwargs
    )

    if AzureAIOpenTelemetryTracer is None or not APPLICATION_INSIGHTS_CONNECTION_STRING:
        return executor

    azure_tracer = AzureAIOpenTelemetryTracer(
        connection_string=APPLICATION_INSIGHTS_CONNECTION_STRING,
        enable_content_recording=True,
        name=AGENT_NAME,
        id=AGENT_ID,
        provider_name=PROVIDER_NAME,
    )
    return executor.with_config(callbacks=[azure_tracer])


def create_agent():
    """Create and return a local LangChain agent."""
    return agent_engines.LangchainAgent(
        model=MODEL_NAME,
        tools=[get_exchange_rate],
        enable_tracing=False,  # Important: Default is False, but when it's turned on, azure tracer stopped working
        runnable_builder=custom_runnable_builder,  # Use custom builder to set azure tracer callback
    )


def query_agent(local_agent, input: str):
    """Query the local agent with a question.
    
    Args:
        local_agent: The LangChain agent to query.
        input: The question or prompt to send to the agent.
    """
    response = local_agent.query(input=input)
    return response


def deploy_agent(local_agent):
    """Deploy the agent to Vertex AI."""
    client = vertexai.Client(
        project=PROJECT,
        location=LOCATION,
    )

    # Provide requirements.txt path for remote packaging
    requirements_path = str(Path(__file__).resolve().parent / "requirements.txt")

    if not STAGING_BUCKET:
        raise RuntimeError(
            "Set VERTEX_STAGING_BUCKET in your environment to deploy the agent.",
        )

    remote_agent = client.agent_engines.create(
        agent=local_agent,
        config={
            "display_name": AGENT_NAME,
            "gcs_dir_name": GCS_DIR_NAME,
            "staging_bucket": STAGING_BUCKET,
            "requirements": requirements_path,
        },
    )

    return remote_agent


if __name__ == "__main__":
    local_agent = create_agent()

    # Query the local agent
    response = query_agent(local_agent, "What is the exchange rate from US dollars to SEK today?")
    print(f"Query response: {response}")
    
    # Deploy the agent to Vertex AI (if configured)
    if STAGING_BUCKET:
        remote_agent = deploy_agent(local_agent)
        print(f"Remote agent name: {remote_agent.api_resource.name}")
        print("To get access token, run: gcloud auth application-default print-access-token")
    else:
        logger.info(
            "VERTEX_STAGING_BUCKET not set; skipping deployment step. "
            "Set it in .env to enable deploy_agent execution.",
        )

    # Flush OpenTelemetry logging handlers before interpreter shutdown
    logging.shutdown()
    atexit.unregister(logging.shutdown)
