"""Evaluate the remote currency agent with Azure AI Evaluation SDK.

This script calls a deployed A2A remote agent, captures responses, and runs
the Task Adherence and Intent Resolution evaluators against the collected
dataset. Results are optionally pushed to an Azure AI Foundry project and
stored locally for later inspection.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from pprint import pprint
from azure.ai.evaluation import (
    IntentResolutionEvaluator,
    TaskAdherenceEvaluator,
    evaluate,
)
from dotenv import load_dotenv

try:  # pragma: no cover - fallback for direct script invocation
    from .utils import (
        AgentEvaluationError,
        fetch_connection_credentials,
        invoke_remote_agent,
    )
except ImportError:  # pragma: no cover - executed when run as a script
    from utils import (
        AgentEvaluationError,
        fetch_connection_credentials,
        invoke_remote_agent,
    )

DATASET_FILE = Path(__file__).with_name('dataset.jsonl')

logger = logging.getLogger(__name__)


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




def _create_dataset_with_agent_id(
    source_dataset: Path, agent_id: str
) -> Path:
    """Create a temporary dataset file with agent_id column injected.
    
    Args:
        source_dataset: Path to the original dataset file
        agent_id: The agent identifier to inject into each row
        
    Returns:
        Path to the temporary dataset file with agent_id column
    """
    temp_dataset_file = source_dataset.with_name('dataset_with_agent_id.jsonl')
    logger.info('Creating temporary dataset with agent_id column at: %s', temp_dataset_file)
    
    with open(source_dataset, 'r', encoding='utf-8') as source_file:
        with open(temp_dataset_file, 'w', encoding='utf-8') as temp_file:
            for line in source_file:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    data['agent_id'] = agent_id
                    temp_file.write(json.dumps(data) + '\n')
    
    logger.info('Temporary dataset created successfully.')
    return temp_dataset_file


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s'
    )

    load_dotenv(Path(__file__).with_name('.env'))

    project_endpoint = os.getenv('AZURE_AI_PROJECT_ENDPOINT')
    if not project_endpoint:
        raise AgentEvaluationError('AZURE_AI_PROJECT_ENDPOINT environment variable is required.')

    # Step 1: Fetch the a2a connection
    agent_id = "gcpa2a"
    base_url, api_key = fetch_connection_credentials(project_endpoint, agent_id)

    if not DATASET_FILE.exists():
        raise AgentEvaluationError(f'Dataset file not found: {DATASET_FILE}')

    # Create temporary dataset with agent_id column
    model_config = load_model_config()

    # Step 2: Define the target function for invoking the remote agent
    def target(query: str) -> dict[str, str]:
        if not isinstance(query, str) or not query.strip():
            raise AgentEvaluationError('Each dataset row must include a non-empty "query" field.')
        logger.info('Querying agent with prompt: %s', query)
        response_text = invoke_remote_agent(base_url, query.strip(), api_key)
        logger.info('Received response: %s', response_text)
        return {'response': response_text}

    # Step 3: Run the evaluation
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

    dataset_path = _create_dataset_with_agent_id(DATASET_FILE, agent_id)
    result = evaluate(
        data=str(dataset_path),
        target=target,
        evaluators=evaluators,
        evaluator_config=evaluator_config,
        azure_ai_project=project_endpoint,
    )

    pprint(result)
    
    # Clean up temporary dataset file
    if dataset_path.exists():
        dataset_path.unlink()
        logger.info('Temporary dataset file deleted.')


if __name__ == '__main__':
    main()
