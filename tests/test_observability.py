"""Tests for core/observability.py — verifies the three exporter branches.

Branch 1: OTEL_EXPORTER_OTLP_ENDPOINT set → OTLP HTTP exporter (or graceful
          fallback to JSONL if opentelemetry-exporter-otlp-proto-http not installed).
Branch 2: OTEL_EXPORTER=console → ConsoleSpanExporter (built-in, no extra deps).
Branch 3: Neither set → local JSONL at <project>/.harness/traces/agent_trajectory.jsonl.
"""
from unittest.mock import MagicMock, patch


def _reset_tracer_state():
    """Reset the module-level initialisation flag between tests."""
    import core.observability as obs
    obs._HARNESS_TRACER_INITIALIZED = False
    # Reset the global OTel tracer provider to avoid cross-test pollution
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    trace.set_tracer_provider(TracerProvider())


class TestExporterBranches:

    def test_jsonl_branch_default(self, tmp_path):
        """Branch 3: no env vars → local JSONL exporter (default, zero deps)."""
        _reset_tracer_state()
        import core.observability as obs

        with patch.dict("os.environ", {}, clear=False):
            # Remove both env vars so we fall through to the JSONL default
            env = {k: v for k, v in __import__("os").environ.items()
                   if k not in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER")}
            with patch.dict("os.environ", env, clear=True):
                tracer = obs.init_tracer(tmp_path)

        jsonl_path = tmp_path / ".harness" / "traces" / "agent_trajectory.jsonl"
        assert jsonl_path.parent.exists(), "JSONL trace directory must be created"
        assert tracer is not None

    def test_console_branch(self, tmp_path, monkeypatch):
        """Branch 2: OTEL_EXPORTER=console → ConsoleSpanExporter attached."""
        _reset_tracer_state()
        import core.observability as obs

        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.setenv("OTEL_EXPORTER", "console")

        added_processors = []

        class _CapturingProvider:
            def add_span_processor(self, p):
                added_processors.append(p)
            def __getattr__(self, name):
                return MagicMock()

        mock_provider = _CapturingProvider()
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        with patch("core.observability.TracerProvider", return_value=mock_provider), \
             patch("core.observability.trace"):
            obs.init_tracer(tmp_path)

        # Verify a BatchSpanProcessor wrapping ConsoleSpanExporter was added
        assert len(added_processors) == 1, "Exactly one processor should be added"
        processor = added_processors[0]
        # BatchSpanProcessor holds the exporter in ._exporter
        exporter = getattr(processor, "_exporter", None) or getattr(
            processor, "span_exporter", None
        )
        assert isinstance(exporter, ConsoleSpanExporter), (
            f"Expected ConsoleSpanExporter, got {type(exporter)}"
        )

    def test_otlp_branch_package_missing_falls_back_to_jsonl(self, tmp_path, monkeypatch):
        """Branch 1 (fallback): OTLP endpoint set but package missing → JSONL fallback.

        This is the graceful degradation path: the gate pipeline must never be blocked
        by a missing observability package.
        """
        _reset_tracer_state()
        import core.observability as obs

        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
        monkeypatch.delenv("OTEL_EXPORTER", raising=False)

        # Simulate missing OTLP package by making the import raise ImportError
        import builtins
        real_import = builtins.__import__

        def _block_otlp(name, *args, **kwargs):
            if "otlp" in name.lower():
                raise ImportError(f"Simulated missing package: {name}")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_block_otlp):
            tracer = obs.init_tracer(tmp_path)

        # Graceful fallback: JSONL directory must still be created
        jsonl_path = tmp_path / ".harness" / "traces" / "agent_trajectory.jsonl"
        assert jsonl_path.parent.exists(), (
            "JSONL directory must be created when OTLP package is missing"
        )
        assert tracer is not None, "init_tracer must not raise when OTLP package missing"

    def test_otlp_branch_package_present(self, tmp_path, monkeypatch):
        """Branch 1 (success): OTLP endpoint set + package present → OTLPSpanExporter."""
        _reset_tracer_state()
        import core.observability as obs

        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
        monkeypatch.delenv("OTEL_EXPORTER", raising=False)

        mock_otlp_exporter = MagicMock()
        mock_otlp_class = MagicMock(return_value=mock_otlp_exporter)

        added_processors = []

        class _CapturingProvider:
            def add_span_processor(self, p):
                added_processors.append(p)
            def __getattr__(self, name):
                return MagicMock()

        mock_provider = _CapturingProvider()

        with patch("core.observability.TracerProvider", return_value=mock_provider), \
             patch("core.observability.trace"), \
             patch.dict(
                 "sys.modules",
                 {"opentelemetry.exporter.otlp.proto.http.trace_exporter":
                  MagicMock(OTLPSpanExporter=mock_otlp_class)},
             ):
            obs.init_tracer(tmp_path)

        mock_otlp_class.assert_called_once_with(
            endpoint="http://collector:4318/v1/traces"
        )
        assert len(added_processors) == 1
