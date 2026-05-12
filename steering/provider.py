"""Steering LLM Provider — factory + NoopProvider for zero-dependency fallback.

Interface contract (what SteeringLoop.__init__ expects from a provider):
    provider.chat(messages: list[dict]) -> str

Usage:
    from steering.provider import create_steering_provider

    provider = create_steering_provider()          # env-var driven, defaults to NoopProvider
    provider = create_steering_provider("noop")    # explicit noop
"""

from __future__ import annotations

import json
import os
import subprocess
from abc import ABC, abstractmethod
from typing import Optional


class SteeringProvider(ABC):
    """Abstract base for Steering LLM providers."""

    @abstractmethod
    def chat(self, messages: list[dict]) -> str:
        """Send messages to LLM, return response text."""


class NoopProvider(SteeringProvider):
    """Pessimistic no-op scorer — returns 0.5 for all dimensions.

    Lets Steering pipeline run without a real LLM: BVS + Constitution + CQG
    checks still execute; only the A/B scoring degrades to tie-breaking.
    """

    def chat(self, messages: list[dict]) -> str:
        return json.dumps({
            "A": {
                "correctness": 0.5, "completeness": 0.5,
                "consistency": 0.5, "concision": 0.5,
                "maintainability": 0.5,
            },
            "B": {
                "correctness": 0.5, "completeness": 0.5,
                "consistency": 0.5, "concision": 0.5,
                "maintainability": 0.5,
            },
            "reason": "noop provider — LLM not configured",
        })


class SubprocessProvider(SteeringProvider):
    """Calls 'claude' CLI via subprocess for LLM scoring."""

    def chat(self, messages: list[dict]) -> str:
        prompt = _format_messages(messages)
        try:
            result = subprocess.run(
                ["claude", "-p", prompt],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        return NoopProvider().chat(messages)


def create_steering_provider(provider_type: Optional[str] = None) -> SteeringProvider:
    """Create a Steering provider from env var or explicit type.

    Reads STEERING_PROVIDER_TYPE env var if provider_type is not given.

    Supported types:
      - noop (default): NoopProvider — zero dependencies, always available
      - subprocess: SubprocessProvider — calls 'claude' CLI via subprocess
    """
    if provider_type is None:
        provider_type = os.environ.get("STEERING_PROVIDER_TYPE", "noop")

    if provider_type == "subprocess":
        return SubprocessProvider()

    # Unknown type or "noop" — fall back to noop
    return NoopProvider()


def _format_messages(messages: list[dict]) -> str:
    """Flatten chat messages into a single prompt string."""
    parts = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)
