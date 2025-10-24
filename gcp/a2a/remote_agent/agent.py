import os
from collections.abc import AsyncIterable
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel


load_dotenv()

memory = MemorySaver()


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
        model_source = os.getenv('model_source', 'google')
        if model_source == 'google':
            model_name = os.getenv('GOOGLE_MODEL_NAME')
            if not model_name:
                raise EnvironmentError(
                    'GOOGLE_MODEL_NAME must be set when model_source=google.',
                )
            self.model = ChatGoogleGenerativeAI(model=model_name)
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
        config = {'configurable': {'thread_id': context_id}}

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
