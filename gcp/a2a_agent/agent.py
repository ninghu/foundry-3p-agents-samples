import logging
import os
from collections.abc import AsyncIterable
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

try:  # Optional dependency for Azure tracing
    from langchain_azure_ai.callbacks.tracers import AzureAIOpenTelemetryTracer
except ImportError:  # pragma: no cover - optional dependency
    AzureAIOpenTelemetryTracer = None  # type: ignore[assignment]


load_dotenv(override=True)

logger = logging.getLogger(__name__)
memory = MemorySaver()


def _str_to_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.lower() in {'1', 'true', 'yes', 'on'}


def _build_tracer() -> Any | None:
    """Return the Azure tracer if dependencies + env configuration are present."""
    if AzureAIOpenTelemetryTracer is None:
        logger.info(
            'langchain-azure-ai not installed; Azure Application Insights tracing disabled.',
        )
        return None

    connection_string = os.getenv('APPLICATION_INSIGHTS_CONNECTION_STRING')
    if not connection_string:
        logger.info(
            'APPLICATION_INSIGHTS_CONNECTION_STRING not set; Azure tracing disabled.',
        )
        return None

    tracer = AzureAIOpenTelemetryTracer(
        connection_string=connection_string,
        enable_content_recording=_str_to_bool(
            os.getenv('APPLICATION_INSIGHTS_ENABLE_CONTENT'),
            default=True,
        ),
        name=os.getenv('APPLICATION_INSIGHTS_AGENT_NAME', 'gcp-a2a-currency-agent'),
        agent_id=os.getenv('APPLICATION_INSIGHTS_AGENT_ID', 'gcp-a2a-currency-agent'),
        provider_name=os.getenv('APPLICATION_INSIGHTS_PROVIDER_NAME', 'gcp.a2a'),
    )
    logger.info('Azure Application Insights tracing enabled for the A2A agent.')
    return tracer


@tool
def get_exchange_rate(
    currency_from: str = 'USD',
    currency_to: str = 'EUR',
    currency_date: str = 'latest',
):
    """Retrieve an exchange rate between two currencies."""
    try:
        response = httpx.get(
            f'https://api.frankfurter.app/{currency_date}',
            params={'from': currency_from, 'to': currency_to},
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
        if 'rates' not in data:
            return {'error': 'Invalid API response format.'}
        return data
    except httpx.HTTPError as exc:
        return {'error': f'API request failed: {exc}'}
    except ValueError:
        return {'error': 'Invalid JSON response from API.'}


class ResponseFormat(BaseModel):
    """Structured response returned by the agent."""

    status: Literal['input_required', 'completed', 'error'] = 'input_required'
    message: str


class CurrencyAgent:
    """CurrencyAgent - a specialized assistant for currency conversions."""

    SYSTEM_INSTRUCTION = (
        'You are a specialized assistant for currency conversions. '
        "Your sole purpose is to use the 'get_exchange_rate' tool to answer questions about currency exchange rates. "
        'If the user asks about anything other than currency conversion or exchange rates, '
        'politely state that you cannot help with that topic and can only assist with currency-related queries. '
        'Do not attempt to answer unrelated questions or use tools for other purposes.'
    )

    FORMAT_INSTRUCTION = (
        'Set response status to input_required if the user needs to provide more information to complete the request.'
        'Set response status to error if there is an error while processing the request.'
        'Set response status to completed if the request is complete.'
    )

    SUPPORTED_CONTENT_TYPES = ['text', 'text/plain']

    def __init__(self):
        model_source = os.getenv('model_source', 'google').lower()
        self.tracer = _build_tracer()
        if model_source == 'google':
            model_name = os.getenv('GOOGLE_MODEL_NAME')
            if not model_name:
                raise EnvironmentError(
                    'GOOGLE_MODEL_NAME must be set when model_source=google.',
                )
            self.model = ChatGoogleGenerativeAI(model=model_name)
        elif model_source == 'azure':
            azure_endpoint = os.getenv('AZURE_OPENAI_ENDPOINT') or os.getenv('TOOL_LLM_URL')
            azure_deployment = os.getenv('AZURE_OPENAI_DEPLOYMENT') or os.getenv('TOOL_LLM_NAME')
            azure_api_key = os.getenv('AZURE_OPENAI_API_KEY') or os.getenv('API_KEY')
            azure_api_version = os.getenv('AZURE_OPENAI_API_VERSION') or os.getenv(
                'OPENAI_API_VERSION',
            ) or '2024-08-01-preview'
            if not azure_endpoint:
                raise EnvironmentError(
                    'AZURE_OPENAI_ENDPOINT (or TOOL_LLM_URL) must be set when model_source=azure.',
                )
            if not azure_deployment:
                raise EnvironmentError(
                    'AZURE_OPENAI_DEPLOYMENT (or TOOL_LLM_NAME) must be set when model_source=azure.',
                )
            if not azure_api_key:
                raise EnvironmentError(
                    'AZURE_OPENAI_API_KEY (or API_KEY) must be set when model_source=azure.',
                )
            self.model = AzureChatOpenAI(
                azure_deployment=azure_deployment,
                azure_endpoint=azure_endpoint,
                api_key=azure_api_key,
                api_version=azure_api_version,
                temperature=0,
            )
        else:
            openai_model = os.getenv('TOOL_LLM_NAME')
            openai_base = os.getenv('TOOL_LLM_URL')
            openai_key = os.getenv('API_KEY')
            if not all([openai_model, openai_base, openai_key]):
                raise EnvironmentError(
                    'TOOL_LLM_NAME, TOOL_LLM_URL, and API_KEY must be set when model_source is not google.',
                )
            self.model = ChatOpenAI(
                model=openai_model,
                openai_api_key=openai_key,
                openai_api_base=openai_base,
                temperature=0,
            )
        self.tools = [get_exchange_rate]
        self.graph = create_react_agent(
            self.model,
            tools=self.tools,
            checkpointer=memory,
            prompt=self.SYSTEM_INSTRUCTION,
            response_format=(self.FORMAT_INSTRUCTION, ResponseFormat),
        )

    async def stream(self, query: str, context_id: str) -> AsyncIterable[dict[str, Any]]:
        inputs = {'messages': [('user', query)]}
        config: dict[str, Any] = {'configurable': {'thread_id': context_id}}
        if self.tracer:
            config['callbacks'] = [self.tracer]

        for item in self.graph.stream(inputs, config, stream_mode='values'):
            message = item['messages'][-1]
            if (
                isinstance(message, AIMessage)
                and message.tool_calls
                and len(message.tool_calls) > 0
            ):
                yield {
                    'is_task_complete': False,
                    'require_user_input': False,
                    'content': 'Looking up the exchange rates...',
                }
            elif isinstance(message, ToolMessage):
                yield {
                    'is_task_complete': False,
                    'require_user_input': False,
                    'content': 'Processing the exchange rates...',
                }

        yield self.get_agent_response(config)

    def get_agent_response(self, config: dict[str, Any]) -> dict[str, Any]:
        current_state = self.graph.get_state(config)
        structured_response = current_state.values.get('structured_response')
        if structured_response and isinstance(
            structured_response, ResponseFormat
        ):
            if structured_response.status == 'input_required':
                return {
                    'is_task_complete': False,
                    'require_user_input': True,
                    'content': structured_response.message,
                }
            if structured_response.status == 'error':
                return {
                    'is_task_complete': False,
                    'require_user_input': True,
                    'content': structured_response.message,
                }
            if structured_response.status == 'completed':
                return {
                    'is_task_complete': True,
                    'require_user_input': False,
                    'content': structured_response.message,
                }

        return {
            'is_task_complete': False,
            'require_user_input': True,
            'content': (
                'We are unable to process your request at the moment. '
                'Please try again.'
            ),
        }
