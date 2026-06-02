"""Tests for steering/provider.py — LLM provider factory + NoopProvider."""

import json
import os
from unittest.mock import patch


class TestNoopProvider:
    def test_chat_returns_valid_json(self):
        from steering.provider import NoopProvider
        provider = NoopProvider()
        result = provider.chat([{"role": "user", "content": "test"}])
        parsed = json.loads(result)
        assert parsed["A"]["correctness"] == 0.5
        assert parsed["B"]["correctness"] == 0.5

    def test_output_is_parseable_by_steering_loop(self):
        """NoopProvider output must match the format LLMJudgeScorer expects."""
        from steering.provider import NoopProvider
        provider = NoopProvider()
        result = provider.chat([{"role": "user", "content": "score these"}])
        parsed = json.loads(result)
        assert "A" in parsed
        assert "B" in parsed
        for dim in ["correctness", "completeness", "consistency", "concision", "maintainability"]:
            assert dim in parsed["A"]
            assert dim in parsed["B"]
            assert 0.0 <= parsed["A"][dim] <= 1.0


class TestCreateSteeringProvider:
    def test_defaults_to_noop(self):
        from steering.provider import create_steering_provider, NoopProvider
        provider = create_steering_provider()
        assert isinstance(provider, NoopProvider)

    def test_explicit_noop(self):
        from steering.provider import create_steering_provider, NoopProvider
        provider = create_steering_provider("noop")
        assert isinstance(provider, NoopProvider)

    def test_env_var_noop(self):
        from steering.provider import create_steering_provider, NoopProvider
        with patch.dict(os.environ, {"STEERING_PROVIDER_TYPE": "noop"}):
            provider = create_steering_provider()
        assert isinstance(provider, NoopProvider)

    def test_unknown_type_falls_back_to_noop(self):
        from steering.provider import create_steering_provider, NoopProvider
        provider = create_steering_provider("nonexistent_backend")
        assert isinstance(provider, NoopProvider)


class TestSubprocessProvider:
    def test_chat_returns_json_on_subprocess_failure(self):
        """SubprocessProvider falls back to NoopProvider when CLI unavailable."""
        from unittest.mock import patch
        from steering.provider import SubprocessProvider
        provider = SubprocessProvider()
        with patch("steering.provider.subprocess.run", side_effect=FileNotFoundError):
            result = provider.chat([{"role": "user", "content": "test"}])
        parsed = json.loads(result)
        assert parsed["A"]["correctness"] == 0.5
        assert parsed["B"]["correctness"] == 0.5

    def test_factory_returns_subprocess_provider(self):
        from steering.provider import create_steering_provider, SubprocessProvider
        provider = create_steering_provider("subprocess")
        assert isinstance(provider, SubprocessProvider)
