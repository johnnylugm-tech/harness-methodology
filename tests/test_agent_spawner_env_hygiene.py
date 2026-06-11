"""Regression: spawned `claude -p` children must not inherit SDK stream markers.

A parent Claude session launched via the Agent SDK (desktop app, SDK harness)
sets CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH / CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH
in its environment. A nested headless `claude -p` inheriting them tries to
fetch OAuth from the (nonexistent) parent stream and dies with
"SDK getOAuthToken callback failed: Stream closed" → API 401.
`core.agent_spawner._child_env` must strip them while keeping the rest.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from core.agent_spawner import AgentSpawner, _SDK_STREAM_MARKERS, _child_env


class TestChildEnv:
    def test_strips_sdk_stream_markers(self, monkeypatch):
        for key in _SDK_STREAM_MARKERS:
            monkeypatch.setenv(key, "1")
        env = _child_env()
        for key in _SDK_STREAM_MARKERS:
            assert key not in env

    def test_keeps_regular_env(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        env = _child_env()
        assert env["PATH"] == "/usr/bin"
        # User BYO endpoint config is the user's domain — never scrubbed.
        assert env["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"

    def test_missing_markers_is_noop(self, monkeypatch):
        for key in _SDK_STREAM_MARKERS:
            monkeypatch.delenv(key, raising=False)
        env = _child_env()
        assert "PATH" in env


class TestSpawnUsesChildEnv:
    def test_subprocess_env_has_no_sdk_markers(self, monkeypatch, tmp_path):
        for key in _SDK_STREAM_MARKERS:
            monkeypatch.setenv(key, "1")

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["env"] = kwargs.get("env")

            class P:
                returncode = 0
                stdout = json.dumps({"result": "ok", "session_id": "s1"})
                stderr = ""

            return P()

        with patch("core.agent_spawner.shutil.which", return_value="/fake/claude"), \
             patch("core.agent_spawner.subprocess.run", side_effect=fake_run):
            spawner = AgentSpawner(project_path=tmp_path)
            result = spawner.spawn(
                role="developer", prompt="noop", context={}, phase=3,
            )

        assert result["status"] in ("complete", "success")
        assert captured["env"] is not None
        for key in _SDK_STREAM_MARKERS:
            assert key not in captured["env"]
        assert "PATH" in captured["env"]
