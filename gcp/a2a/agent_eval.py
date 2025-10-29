"""Evaluate the remote currency agent with Azure AI Evaluation SDK.

This script calls a deployed A2A remote agent, captures responses, and runs
the Task Adherence and Intent Resolution evaluators against the collected
dataset. Results are optionally pushed to an Azure AI Foundry project and
stored locally for later inspection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from pprint import pprint
from typing import Any, Iterable, Iterator
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import MessageSendParams, SendMessageRequest
from azure.ai.evaluation import (
    IntentResolutionEvaluator,
    TaskAdherenceEvaluator,
    evaluate,
)
from azure.identity import CredentialUnavailableError, DefaultAzureCredential
from dotenv import load_dotenv

DATASET_FILE = Path(__file__).with_name('dataset.jsonl')
REQUEST_TIMEOUT_SECONDS = 120.0
CONNECTION_RESOURCE_TEMPLATE = (
    'connections/{connection_name}/getConnectionWithCredentials?api-version=v1'
)
AZURE_AI_SCOPE = 'https://ai.azure.com/.default'

logger = logging.getLogger(__name__)


class AgentEvaluationError(RuntimeError):
    """Raised when the evaluation workflow cannot be completed."""


def load_model_config() -> dict[str, str]:
    """Read Azure OpenAI settings from environment variables."""
    env_to_model_key = {
        'AZURE_OPENAI_ENDPOINT': 'azure_endpoint',
        'AZURE_OPENAI_DEPLOYMENT': 'azure_deployment',
        'AZURE_OPENAI_KEY': 'api_key',
    }
    model_config: dict[str, str] = {}
    missing: list[str] = []
    for env_key, model_key in env_to_model_key.items():
        value = os.getenv(env_key)
        if value:
            model_config[model_key] = value
        else:
            missing.append(env_key)
    if missing:
        raise AgentEvaluationError(
            'Missing Azure OpenAI configuration for evaluators: '
            + ', '.join(missing)
        )
    return model_config


def extract_text_response(response: Any) -> str:
    """Extract the last text fragment from an A2A response payload."""
    payload = _normalize_payload(response)

    artifact_text = _extract_artifact_text(payload)
    if artifact_text:
        return artifact_text.strip()

    texts = list(_iter_text_parts(payload))
    if not texts:
        raise AgentEvaluationError(
            'Unable to locate a text response in the agent payload.'
        )
    return texts[-1].strip()


def _iter_text_parts(node: Any) -> Iterator[str]:
    """Yield every text fragment contained in the payload."""
    if isinstance(node, dict):
        text = node.get('text')
        if node.get('kind') == 'text' and isinstance(text, str):
            yield text
        for value in node.values():
            yield from _iter_text_parts(value)
    elif isinstance(node, (list, tuple, set)):
        for item in node:
            yield from _iter_text_parts(item)


def _extract_artifact_text(node: Any) -> str | None:
    """Locate the text stored inside any task artifacts."""
    if isinstance(node, dict):
        artifacts = node.get('artifacts')
        if isinstance(artifacts, list):
            for artifact in reversed(artifacts):
                if not isinstance(artifact, dict):
                    continue
                parts = artifact.get('parts')
                if not isinstance(parts, list):
                    continue
                for part in reversed(parts):
                    if (
                        isinstance(part, dict)
                        and part.get('kind') == 'text'
                        and isinstance(part.get('text'), str)
                        and part['text'].strip()
                    ):
                        return part['text']
        for value in node.values():
            result = _extract_artifact_text(value)
            if result:
                return result
    elif isinstance(node, (list, tuple, set)):
        items: Iterable[Any]
        if isinstance(node, (list, tuple)):
            items = reversed(node)
        else:
            items = node
        for item in items:
            result = _extract_artifact_text(item)
            if result:
                return result
    return None


def _normalize_payload(obj: Any) -> Any:
    """Widen Pydantic models into basic Python containers."""
    if isinstance(obj, dict):
        return obj
    for attr_name in ('model_dump', 'dict'):
        attr = getattr(obj, attr_name, None)
        if callable(attr):
            try:
                return attr(exclude_none=True)  # type: ignore[call-arg]
            except TypeError:
                return attr()
    return json.loads(json.dumps(obj, default=_default_json_encoder))


def _default_json_encoder(obj: Any) -> Any:
    """Fallback encoder for objects that are not JSON serializable."""
    if hasattr(obj, '__dict__'):
        return obj.__dict__
    return str(obj)


_default_credential: DefaultAzureCredential | None = None


def _get_ai_access_token() -> str:
    """Acquire an access token for Azure AI Foundry APIs."""

    global _default_credential
    if _default_credential is None:
        # Lazily construct the credential so CLI/MSI auth works if available.
        _default_credential = DefaultAzureCredential(
            exclude_interactive_browser_credential=True
        )

    try:
        access_token = _default_credential.get_token(AZURE_AI_SCOPE)
    except CredentialUnavailableError as exc:
        raise AgentEvaluationError(
            'Azure credentials are unavailable. Set AZURE_AI_FOUNDRY_TOKEN or '
            'configure a supported Azure identity to authenticate with ai.azure.com.'
        ) from exc
    except Exception as exc:
        raise AgentEvaluationError(
            f'Failed to acquire Azure AI Foundry access token: {exc}'
        ) from exc

    return access_token.token


async def _call_remote_agent(base_url: str, prompt: str, api_key: str | None) -> str:
    """Send a single prompt to the remote agent and return its response text."""
    timeout = httpx.Timeout(timeout=REQUEST_TIMEOUT_SECONDS)
    default_headers = {'api-key': api_key} if api_key else None
    async with httpx.AsyncClient(timeout=timeout, headers=default_headers) as httpx_client:
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=base_url)
        agent_card = await resolver.get_agent_card()
        client = A2AClient(httpx_client=httpx_client, agent_card=agent_card)

        payload = {
            'message': {
                'role': 'user',
                'parts': [{'kind': 'text', 'text': prompt}],
                'message_id': uuid4().hex,
            },
        }
        request = SendMessageRequest(
            id=str(uuid4()),
            params=MessageSendParams(**payload),
        )
        try:
            response = await client.send_message(request)
        except httpx.HTTPError as exc:
            logger.error('Error occurred while sending message: %s', exc)
            return 'Error occurred while sending message.'
        return extract_text_response(response)


def invoke_remote_agent(base_url: str, prompt: str, api_key: str | None) -> str:
    """Synchronously invoke the async remote agent helper."""

    async def _runner() -> str:
        return await _call_remote_agent(base_url, prompt, api_key)

    try:
        return asyncio.run(_runner())
    except RuntimeError as exc:
        if 'asyncio.run() cannot be called from a running event loop' in str(exc):
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                return loop.run_until_complete(_runner())
            finally:
                asyncio.set_event_loop(None)
                loop.close()
        raise


def _fetch_connection_credentials(
    project_endpoint: str, connection_name: str
) -> tuple[str, str]:
    """Retrieve the remote agent URL and API key from Azure AI Foundry."""
    logger.info('Fetching connection configuration for "%s".', connection_name)
    normalized_endpoint = project_endpoint.rstrip('/')
    connection_path = CONNECTION_RESOURCE_TEMPLATE.format(
        connection_name=connection_name
    )
    connection_url = f'{normalized_endpoint}/{connection_path}'
    access_token = _get_ai_access_token()
    try:
        response = httpx.post(
            connection_url,
            json={'name': connection_name},
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise AgentEvaluationError(
            f'Unable to retrieve connection "{connection_name}": {exc}'
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise AgentEvaluationError(
            'Connection service returned invalid JSON payload.'
        ) from exc

    base_url = payload.get('target')
    credentials = payload.get('credentials') or {}
    api_key = credentials.get('api-key')

    if not base_url:
        raise AgentEvaluationError(
            'Connection payload does not include the remote agent URL.'
        )
    if not api_key:
        raise AgentEvaluationError(
            'Connection payload does not include the remote agent API key.'
        )

    return base_url, api_key


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s'
    )

    load_dotenv(Path(__file__).with_name('.env'))

    project_endpoint = os.getenv('AZURE_AI_PROJECT_ENDPOINT')
    if not project_endpoint:
        raise AgentEvaluationError('AZURE_AI_PROJECT_ENDPOINT environment variable is required.')

    a2a_connection_name = "gcpa2a"
    base_url, api_key = _fetch_connection_credentials(project_endpoint, a2a_connection_name)

    if not DATASET_FILE.exists():
        raise AgentEvaluationError(f'Dataset file not found: {DATASET_FILE}')

    model_config = load_model_config()

    def target(query: str) -> dict[str, str]:
        if not isinstance(query, str) or not query.strip():
            raise AgentEvaluationError('Each dataset row must include a non-empty "query" field.')
        logger.info('Querying agent with prompt: %s', query)
        response_text = invoke_remote_agent(base_url, query.strip(), api_key)
        logger.info('Received response: %s', response_text)
        return {'response': response_text}

    evaluators = {
        'task_adherence': TaskAdherenceEvaluator(model_config=model_config),
        'intent_resolution': IntentResolutionEvaluator(model_config=model_config),
    }
    evaluator_config = {
        'default': {
            'column_mapping': {
                'query': '${data.query}',
                'response': '${target.response}',
            }
        }
    }

    result = evaluate(
        data=str(DATASET_FILE),
        target=target,
        evaluators=evaluators,
        evaluator_config=evaluator_config,
        azure_ai_project=project_endpoint,
    )

    pprint(result)


if __name__ == '__main__':
    main()
