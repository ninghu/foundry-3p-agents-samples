"""Basic red teaming harness for the remote A2A agent.

This script borrows the connection/invocation helpers from ``a2a_agent_eval``
and wires them into the Azure AI Evaluation red teaming workflow. It mirrors
the simplest flip-strategy example from the Azure sample notebook so it can be
used as a lightweight smoke test before investing in larger scans.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from pprint import pprint
from typing import Callable

from azure.ai.evaluation.red_team import AttackStrategy, RedTeam, RiskCategory
from azure.identity import CredentialUnavailableError, DefaultAzureCredential
from dotenv import load_dotenv

try:  # pragma: no cover - prefer package relative import
    from .utils import (
        AgentEvaluationError,
        fetch_connection_credentials,
        invoke_remote_agent,
    )
except ImportError:  # pragma: no cover - fallback for direct script execution
    from utils import (
        AgentEvaluationError,
        fetch_connection_credentials,
        invoke_remote_agent,
    )

DEFAULT_AGENT_ID = 'gcpa2a'
DEFAULT_SCAN_NAME = 'A2A-Basic-RedTeam'
DEFAULT_OBJECTIVE_COUNT = 1
OUTPUT_FILE = Path(__file__).with_name('a2a_redteam_output.json')

logger = logging.getLogger(__name__)


def _build_credential() -> DefaultAzureCredential:
    """Create a credential for use with the Azure AI Evaluation SDK."""
    try:
        return DefaultAzureCredential(exclude_interactive_browser_credential=True)
    except Exception as exc:  # pragma: no cover - instantiation errors are rare
        raise AgentEvaluationError(
            'Unable to initialize DefaultAzureCredential for Azure AI Evaluation.'
        ) from exc


def _build_target(base_url: str, api_key: str | None) -> Callable[[str], str]:
    """Return a callable compatible with RedTeam.scan that hits the A2A agent."""

    def _target(query: str) -> str:
        if not isinstance(query, str) or not query.strip():
            raise AgentEvaluationError('Red team prompts must be non-empty.')
        logger.info('Sending red team prompt: %s', query)
        response_text = invoke_remote_agent(base_url, query, api_key)
        logger.info('Received response from agent: %s', response_text)
        return response_text

    return _target


async def _run_scan(
    red_team: RedTeam,
    target: Callable[[str], str],
    scan_name: str,
    output_path: Path,
) -> dict:
    """Execute the red team scan and persist the raw output."""
    result = await red_team.scan(
        target=target,
        scan_name=scan_name,
        attack_strategies=[AttackStrategy.Flip],
        output_path=str(output_path),
    )
    return result


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )

    load_dotenv(Path(__file__).with_name('.env'))

    project_endpoint = os.getenv('AZURE_AI_PROJECT_ENDPOINT')
    if not project_endpoint:
        raise AgentEvaluationError(
            'AZURE_AI_PROJECT_ENDPOINT environment variable is required.'
        )

    connection_name = os.getenv('A2A_AGENT_CONNECTION', DEFAULT_AGENT_ID)
    scan_name = os.getenv('A2A_REDTEAM_SCAN_NAME', DEFAULT_SCAN_NAME)
    objectives_raw = os.getenv(
        'A2A_REDTEAM_OBJECTIVES', str(DEFAULT_OBJECTIVE_COUNT)
    )
    try:
        objective_count = max(1, int(objectives_raw))
    except ValueError as exc:
        raise AgentEvaluationError(
            'A2A_REDTEAM_OBJECTIVES must be an integer.'
        ) from exc

    base_url, api_key = fetch_connection_credentials(
        project_endpoint, connection_name
    )
    credential = _build_credential()

    red_team = RedTeam(
        azure_ai_project=project_endpoint,
        credential=credential,
        risk_categories=[RiskCategory.Violence, RiskCategory.HateUnfairness],
        num_objectives=objective_count,
    )
    target = _build_target(base_url, api_key)

    logger.info(
        'Starting red team scan "%s" against connection "%s".',
        scan_name,
        connection_name,
    )

    try:
        result = asyncio.run(
            _run_scan(red_team, target, scan_name, OUTPUT_FILE)
        )
    except CredentialUnavailableError as exc:
        raise AgentEvaluationError(
            'Azure credentials are unavailable for the red teaming SDK.'
        ) from exc

    logger.info('Red team scan complete. Raw output saved to %s', OUTPUT_FILE)
    pprint(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
