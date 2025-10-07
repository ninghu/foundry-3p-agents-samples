import vertexai
from vertexai import agent_engines
from typing import Sequence
from langchain_core.language_models import BaseLanguageModel
from langchain_core.prompts import BasePromptTemplate
from langchain_core.tools import BaseTool

from langchain_azure_ai.callbacks.tracers import AzureAIOpenTelemetryTracer


project="ninhu-project1"
location="us-west1"
model_name = "gemini-2.0-flash"
application_insights_connection_string = "InstrumentationKey=e2d97709-3700-4cb2-97cd-0c9731012cd3;IngestionEndpoint=https://eastus-8.in.applicationinsights.azure.com/;LiveEndpoint=https://eastus.livediagnostics.monitor.azure.com/;ApplicationId=9e7ddef3-76bd-4af3-a9ff-4f0095502cfa"
agent_name = "gcp-currency-exchange-agent"
agent_id = f"gcp-agent-m3p8w"
provider_name = "gcp.vertex_ai"

vertexai.init(
    project=project,
    location=location,
)

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


def create_agent():
    """Create and return a local LangChain agent."""
    
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

        # Convert Python functions to LangChain tools if needed
        converted_tools = []
        for t in tools:
            if callable(t) and not hasattr(t, 'name'):
                converted_tools.append(tool(t))
            else:
                converted_tools.append(t)
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a helpful assistant for currency exchange rates."),
            ("user", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        agent = create_tool_calling_agent(model, converted_tools, prompt)
        executor = AgentExecutor(
            agent=agent, 
            tools=converted_tools,
            **agent_executor_kwargs
        )

        # Enable sending traces to Azure Application Insights
        azure_tracer = AzureAIOpenTelemetryTracer(
            connection_string=application_insights_connection_string,
            enable_content_recording=True,
            name=agent_name,
            id=agent_id,
            provider_name=provider_name,
        )  
        return executor.with_config(callbacks=[azure_tracer])
    
    return agent_engines.LangchainAgent(
        model=model_name,
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
        project=project,
        location=location,
    )

    remote_agent = client.agent_engines.create(
        agent=local_agent,
        config={
            "display_name": agent_name,
            "gcs_dir_name": "dev",
            "staging_bucket": "gs://ninhu-project1-vertex-agents",
            "requirements": [
                "google-cloud-aiplatform[agent_engines,langchain]",
                "langchain-azure-ai[opentelemetry]",
                "python-dotenv",
                "cloudpickle",
                "pydantic",
            ],
        },
    )

    return remote_agent


if __name__ == "__main__":
    local_agent = create_agent()
    
    # response = query_agent(local_agent, "What is the exchange rate from US dollars to SEK today?")
    # print(f"Query response: {response}")
    
    skip_deploy = False
    if not skip_deploy:
        remote_agent = deploy_agent(local_agent)
        print(f"Remote agent name: {remote_agent.api_resource.name}")
        print("To get access token, run: gcloud auth application-default print-access-token")
