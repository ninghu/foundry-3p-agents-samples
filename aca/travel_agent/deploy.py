#!/usr/bin/env python3

"""Interactive deploy script for the ACA travel planner agent.

Reads settings from .env, prompts for any missing values, creates Azure
resources (resource group, ACR, ACA environment) as needed, and deploys
the container app.  Prints the agent URL at the end.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Dict


SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATH = SCRIPT_DIR / ".env"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_env(path: Path) -> Dict[str, str]:
    """Parse KEY=VALUE lines from a .env file."""
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def _ask(prompt: str, default: str = "") -> str:
    """Prompt the user; return default if they press Enter."""
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def _require(env: Dict[str, str], key: str, prompt: str, default: str = "") -> str:
    """Return env[key] if set, otherwise ask the user interactively."""
    val = env.get(key, "")
    if val:
        return val
    val = _ask(prompt, default)
    env[key] = val          # remember for later save
    return val


def _save_env(env: Dict[str, str], path: Path) -> None:
    """Write deployment settings back to .env so they persist across runs."""
    # Read existing file to preserve comments and non-deployment keys
    existing_lines: list[str] = []
    existing_keys: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in env:
                    existing_lines.append(f"{k}={env[k]}")
                    existing_keys.add(k)
                else:
                    existing_lines.append(line)
            else:
                existing_lines.append(line)
    # Append any new keys that weren't in the original file
    for k, v in env.items():
        if k not in existing_keys:
            existing_lines.append(f"{k}={v}")
    path.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")


def _az() -> str:
    from shutil import which
    for name in ("az.cmd", "az"):
        found = which(name)
        if found:
            return found
    print("ERROR: Azure CLI (az) not found. Install from "
          "https://learn.microsoft.com/cli/azure/install-azure-cli")
    sys.exit(1)


def _run(cmd: list[str], *, capture: bool = False) -> str:
    """Run a CLI command, exit on failure. Return stdout if capture=True."""
    r = subprocess.run(cmd, check=True, capture_output=capture, text=True)
    return r.stdout.strip() if capture else ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("\n=== ACA Travel Planner — Deploy ===\n")

    env = _parse_env(ENV_PATH)
    az = _az()

    # ---- Collect settings (prompt if missing) ----
    rg       = _require(env, "AZURE_RESOURCE_GROUP",  "Resource group name",    "rg-aca-travel-agent")
    location = _require(env, "AZURE_LOCATION",        "Azure location",         "eastus")
    acr      = _require(env, "ACR_NAME",              "Container Registry name", "acatravelagentcr")
    aca_env  = _require(env, "ACA_ENVIRONMENT_NAME",  "ACA environment name",   "aca-agents-env")
    app_name = _require(env, "ACA_APP_NAME",          "Container app name",     "aca-travel-planner")

    # Save settings so they persist for next run
    _save_env(env, ENV_PATH)

    tag = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    image = f"{app_name}:{tag}"
    image_uri = f"{acr}.azurecr.io/{image}"

    # ---- Step 1: Resource group ----
    print(f"\n[1/5] Ensuring resource group: {rg}")
    _run([az, "group", "create", "--name", rg, "--location", location])

    # ---- Step 2: Container Registry ----
    print(f"[2/5] Ensuring container registry: {acr}")
    _run([az, "acr", "create", "--name", acr, "--resource-group", rg,
          "--location", location, "--sku", "Basic", "--admin-enabled", "true"])

    # ---- Step 3: Build image ----
    print(f"[3/5] Building image: {image_uri}")
    _run([az, "acr", "build", "--registry", acr, "--resource-group", rg,
          "--image", image, str(SCRIPT_DIR)])

    # ---- Step 4: ACA environment ----
    print(f"[4/5] Ensuring ACA environment: {aca_env}")
    _run([az, "containerapp", "env", "create",
          "--name", aca_env, "--resource-group", rg, "--location", location])

    # ---- Step 5: Deploy container app ----
    print(f"[5/5] Deploying container app: {app_name}")

    # Env vars to forward to the container
    forward_keys = [
        "PROJECT_ENDPOINT", "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_VERSION",
        "APPLICATION_INSIGHTS_CONNECTION_STRING",
        "OTEL_AGENT_ID", "APPLICATION_INSIGHTS_ENABLE_CONTENT",
    ]
    env_pairs = [f"{k}={env[k]}" for k in forward_keys if env.get(k)]

    deploy_cmd = [
        az, "containerapp", "create",
        "--name", app_name,
        "--resource-group", rg,
        "--environment", aca_env,
        "--image", image_uri,
        "--registry-server", f"{acr}.azurecr.io",
        "--target-port", "8080",
        "--ingress", "external",
        "--min-replicas", "1",
    ]
    if env_pairs:
        deploy_cmd.extend(["--env-vars", *env_pairs])

    _run(deploy_cmd)

    # ---- Retrieve URL ----
    fqdn = _run([az, "containerapp", "show",
                 "--name", app_name, "--resource-group", rg,
                 "--query", "properties.configuration.ingress.fqdn",
                 "--output", "tsv"], capture=True)

    print(f"\n{'=' * 60}")
    print(f"  Deployment complete!")
    print(f"  Agent URL:       https://{fqdn}")
    print(f"  Health check:    https://{fqdn}/healthz")
    print(f"  Invoke endpoint: https://{fqdn}/invoke")
    print(f"{'=' * 60}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        print(f"\nAzure CLI command failed (exit {exc.returncode}).", file=sys.stderr)
        sys.exit(1)
