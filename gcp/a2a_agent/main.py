import logging
import os
import sys
from urllib.parse import parse_qs

import click
import httpx
import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import (
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
)
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from dotenv import load_dotenv
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

if __package__ is None or __package__ == '':
    from agent import CurrencyAgent  # type: ignore
    from agent_executor import CurrencyAgentExecutor  # type: ignore
else:
    from .agent import CurrencyAgent
    from .agent_executor import CurrencyAgentExecutor


load_dotenv(override=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MissingAPIKeyError(Exception):
    """Raised when the required API configuration is missing."""


class APIKeyGuard:
    """ASGI wrapper enforcing an API key on inbound HTTP requests."""

    def __init__(
        self,
        app,
        expected_key: str,
        allowed_paths: tuple[str, ...] | None = None,
    ) -> None:
        self._app = app
        self._expected_key = expected_key
        self._allowed_paths = set(allowed_paths or ())

    async def __call__(self, scope, receive, send):
        if scope.get('type') != 'http' or not self._expected_key:
            await self._app(scope, receive, send)
            return

        path = scope.get('path', '')
        if path in self._allowed_paths:
            await self._app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        provided_key = headers.get('api-key')
        if not provided_key:
            raw_query = scope.get('query_string', b'')
            if raw_query:
                query_params = parse_qs(raw_query.decode('utf-8'))
                provided_key = query_params.get('api_key', [None])[0]

        if provided_key != self._expected_key:
            response = JSONResponse(
                {'detail': 'Unauthorized. Supply the correct API key via the api-key header or api_key query parameter.'},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)


def _load_agent_api_key() -> str:
    """Return the configured API key used to guard incoming requests."""
    return os.getenv('A2A_AGENT_API_KEY', 'gcpa2aagentkey')


def _build_public_base_url(bind_host: str, port: int) -> str:
    """Return an externally reachable base URL for the agent card."""
    public_host = os.getenv('PUBLIC_HOST')
    if not public_host:
        if bind_host in {'0.0.0.0', '::', '::0'}:
            public_host = 'localhost'
        else:
            public_host = bind_host

    public_scheme = os.getenv('PUBLIC_SCHEME', 'http')
    public_port = os.getenv('PUBLIC_PORT') or str(port)

    if public_port in {'80', '443'}:
        return f'{public_scheme}://{public_host}/'
    return f'{public_scheme}://{public_host}:{public_port}/'


@click.command()
@click.option('--host', 'host', default=None)
@click.option('--port', 'port', type=int, default=None)
def main(host: str | None, port: int | None) -> None:
    """Starts the Currency Agent server."""
    effective_host = host or os.getenv('BIND_HOST') or os.getenv('HOST', '0.0.0.0')
    effective_port = port or int(os.getenv('PORT', '8080'))

    try:
        _validate_required_config()

        capabilities = AgentCapabilities(streaming=True, push_notifications=True)
        skill = AgentSkill(
            id='convert_currency',
            name='Currency Exchange Rates Tool',
            description='Helps with exchange values between various currencies',
            tags=['currency conversion', 'currency exchange'],
            examples=['What is exchange rate between USD and GBP?'],
        )
        base_url = _build_public_base_url(effective_host, effective_port)
        agent_card = AgentCard(
            name='Currency Agent',
            description='Helps with exchange rates for currencies',
            url=base_url,
            version='1.0.0',
            default_input_modes=CurrencyAgent.SUPPORTED_CONTENT_TYPES,
            default_output_modes=CurrencyAgent.SUPPORTED_CONTENT_TYPES,
            capabilities=capabilities,
            skills=[skill],
        )

        httpx_client = httpx.AsyncClient()
        push_config_store = InMemoryPushNotificationConfigStore()
        push_sender = BasePushNotificationSender(
            httpx_client=httpx_client,
            config_store=push_config_store,
        )
        request_handler = DefaultRequestHandler(
            agent_executor=CurrencyAgentExecutor(),
            task_store=InMemoryTaskStore(),
            push_config_store=push_config_store,
            push_sender=push_sender,
        )
        server = A2AStarletteApplication(
            agent_card=agent_card,
            http_handler=request_handler,
        )

        app = server.build()

        api_key = _load_agent_api_key().strip()
        if api_key:
            allowed_paths: tuple[str, ...] = ('/healthz', '/', '/_ah/health')
            app = APIKeyGuard(app, expected_key=api_key, allowed_paths=allowed_paths)
            logger.info('API key protection enabled for the remote agent.')

        uvicorn.run(app, host=effective_host, port=effective_port)

    except MissingAPIKeyError as exc:
        logger.error('Error: %s', exc)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        logger.error('An error occurred during server startup: %s', exc)
        sys.exit(1)


def _validate_required_config() -> None:
    model_source = os.getenv('model_source', 'google').lower()
    if model_source == 'google':
        if not os.getenv('GOOGLE_API_KEY'):
            raise MissingAPIKeyError('GOOGLE_API_KEY environment variable not set.')
    elif model_source == 'azure':
        if not (os.getenv('AZURE_OPENAI_ENDPOINT') or os.getenv('TOOL_LLM_URL')):
            raise MissingAPIKeyError('AZURE_OPENAI_ENDPOINT or TOOL_LLM_URL must be set when model_source=azure.')
        if not (os.getenv('AZURE_OPENAI_DEPLOYMENT') or os.getenv('TOOL_LLM_NAME')):
            raise MissingAPIKeyError('AZURE_OPENAI_DEPLOYMENT or TOOL_LLM_NAME must be set when model_source=azure.')
        if not (os.getenv('AZURE_OPENAI_API_KEY') or os.getenv('API_KEY')):
            raise MissingAPIKeyError('AZURE_OPENAI_API_KEY or API_KEY must be set when model_source=azure.')
    else:
        if not os.getenv('TOOL_LLM_URL'):
            raise MissingAPIKeyError('TOOL_LLM_URL environment variable not set.')
        if not os.getenv('TOOL_LLM_NAME'):
            raise MissingAPIKeyError('TOOL_LLM_NAME environment variable not set.')


if __name__ == '__main__':
    main()
