"""Helper utilities for wiring Azure Application Insights tracing."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _str_to_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def configure_azure_tracer() -> Optional[Any]:
    """Return an AzureAIOpenTelemetryTracer if configuration is present."""
    connection_string = os.getenv("APPLICATION_INSIGHTS_CONNECTION_STRING")
    if not connection_string:
        logger.info(
            "APPLICATION_INSIGHTS_CONNECTION_STRING not provided; skipping Azure tracing.",
        )
        return None

    try:
        from langchain_azure_ai.callbacks.tracers import AzureAIOpenTelemetryTracer  # type: ignore
    except ImportError:  # pragma: no cover - optional dependency
        logger.warning(
            "langchain-azure-ai >= 1.0.0 is required for Azure tracing; run without tracing.",
        )
        return None

    tracer = AzureAIOpenTelemetryTracer(
        connection_string=connection_string,
        enable_content_recording=_str_to_bool(
            os.getenv("APPLICATION_INSIGHTS_ENABLE_CONTENT"),
            default=True,
        ),
        name=os.getenv("APPLICATION_INSIGHTS_AGENT_NAME", "gcp-cloud-run-exchange-agent"),
        id=os.getenv("APPLICATION_INSIGHTS_AGENT_ID", "gcp-cloud-run-exchange-agent"),
        provider_name=os.getenv("APPLICATION_INSIGHTS_PROVIDER_NAME", "gcp.cloud_run"),
    )

    logger.info("Azure Application Insights tracing enabled.")
    return tracer
