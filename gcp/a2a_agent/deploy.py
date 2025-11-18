#!/usr/bin/env python3

"""Build and deploy the remote agent to Google Cloud Run."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Dict, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ENV_LOCATIONS = [
    SCRIPT_DIR / '.env',
    SCRIPT_DIR.parent / '.env',
    SCRIPT_DIR.parent.parent / '.env',
]
PROJECT_KEYS = {'GCP_PROJECT_ID', 'PROJECT_ID'}
REGION_KEYS = {'GCP_REGION', 'REGION'}


def parse_env_file(path: Path) -> Dict[str, str]:
    """Parse a simple KEY=VALUE .env file."""
    if not path.exists():
        search_hint = ', '.join(str(p) for p in DEFAULT_ENV_LOCATIONS)
        raise FileNotFoundError(f'Env file not found: {path}. Tried: {search_hint}')

    env: Dict[str, str] = {}
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        env[key.strip()] = value.strip()
    return env


def select_first(env: Dict[str, str], keys: Iterable[str]) -> str | None:
    """Return the first matching key present in env."""
    for key in keys:
        if env.get(key):
            return env[key]
    return None


def ensure_gcloud() -> str:
    """Return the full path to the gcloud executable."""
    from shutil import which

    for candidate in ('gcloud.cmd', 'gcloud'):
        resolved = which(candidate)
        if resolved:
            return resolved
    raise RuntimeError('gcloud CLI not found on PATH. Install and configure the Google Cloud SDK first.')


def resolve_default_env() -> Path:
    """Return the first existing default env path or the last candidate."""
    for candidate in DEFAULT_ENV_LOCATIONS:
        if candidate.exists():
            return candidate
    return DEFAULT_ENV_LOCATIONS[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description='Build and deploy the remote agent to Cloud Run.')
    parser.add_argument(
        '--env-file',
        type=Path,
        default=resolve_default_env(),
        help='Path to .env file (default: tries ./ .env, ../.env, then ../../.env)',
    )
    parser.add_argument('--service-name', default='currency-agent', help='Cloud Run service name')
    parser.add_argument('--repo-name', default='agents', help='Artifact Registry repository name')
    parser.add_argument('--image-tag', default=None, help='Image tag (default: current timestamp)')
    parser.add_argument('--extra-env', default=None, help='Comma-separated KEY=VALUE pairs appended to Cloud Run env vars')

    args = parser.parse_args()

    gcloud_executable = ensure_gcloud()

    env_values = parse_env_file(args.env_file)

    project_id = select_first(env_values, PROJECT_KEYS)
    region = select_first(env_values, REGION_KEYS)

    if not project_id or not region:
        raise RuntimeError(
            'Both GCP_PROJECT_ID (or PROJECT_ID) and GCP_REGION (or REGION) must be set in the env file.'
        )

    image_tag = args.image_tag or datetime.now(UTC).strftime('%Y%m%d%H%M%S')
    image_uri = f'{region}-docker.pkg.dev/{project_id}/{args.repo_name}/{args.service_name}:{image_tag}'

    deploy_env = {
        key: value
        for key, value in env_values.items()
        if key not in PROJECT_KEYS | REGION_KEYS
    }

    if args.extra_env:
        for pair in args.extra_env.split(','):
            if '=' not in pair:
                raise RuntimeError(f"Invalid extra env entry '{pair}'. Expected KEY=VALUE.")
            key, value = pair.split('=', 1)
            deploy_env[key.strip()] = value.strip()

    env_arg = ','.join(f'{k}={v}' for k, v in deploy_env.items()) if deploy_env else None

    script_dir = Path(__file__).resolve().parent

    build_cmd = [
        gcloud_executable,
        'builds',
        'submit',
        str(script_dir),
        '--tag',
        image_uri,
        '--project',
        project_id,
    ]
    deploy_cmd = [
        gcloud_executable,
        'run',
        'deploy',
        args.service_name,
        '--image',
        image_uri,
        '--region',
        region,
        '--project',
        project_id,
        '--platform',
        'managed',
        '--allow-unauthenticated',
    ]
    if env_arg:
        deploy_cmd.extend(['--set-env-vars', env_arg])

    print(f'Building image: {image_uri}')
    try:
        subprocess.run(build_cmd, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError('Failed to execute gcloud. Ensure the Google Cloud SDK is installed and accessible.') from exc

    print(f'Deploying service: {args.service_name}')
    try:
        subprocess.run(deploy_cmd, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError('Failed to execute gcloud during deploy step.') from exc

    print('Deployment complete.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
