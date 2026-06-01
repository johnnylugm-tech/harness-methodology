import json
import os
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.resources import Resource


# A simple JSON file exporter to record agent trajectories
class JsonFileSpanExporter(SpanExporter):
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "agent_trajectory.jsonl"

    def export(self, spans) -> SpanExportResult:
        with open(self.log_file, "a", encoding="utf-8") as f:
            for span in spans:
                span_data = {
                    "name": span.name,
                    "context": {
                        "trace_id": format(span.context.trace_id, "032x") if span.context else None,
                        "span_id": format(span.context.span_id, "016x") if span.context else None,
                    },
                    "start_time": span.start_time,
                    "end_time": span.end_time,
                    "attributes": dict(span.attributes) if span.attributes else {},
                    "events": [
                        {
                            "name": event.name,
                            "timestamp": event.timestamp,
                            "attributes": dict(event.attributes) if event.attributes else {},
                        }
                        for event in span.events
                    ],
                }
                f.write(json.dumps(span_data) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass


# Module-level flag: tracks whether WE set up the provider and attached our exporter.
# Avoids falsely skipping setup when a third-party lib has already set a TracerProvider.
_HARNESS_TRACER_INITIALIZED: bool = False


def _add_jsonl_exporter(provider: TracerProvider, project_root: Path) -> None:
    """Attach the local JSONL exporter to *provider* (default, zero extra dependencies)."""
    log_dir = project_root / ".harness" / "traces"
    provider.add_span_processor(BatchSpanProcessor(JsonFileSpanExporter(log_dir)))


def init_tracer(project_root: Path) -> trace.Tracer:
    """Initialise and return the OpenTelemetry tracer.

    Exporter selection (checked in order):
      1. ``OTEL_EXPORTER_OTLP_ENDPOINT`` set → OTLP HTTP exporter
         (requires ``opentelemetry-exporter-otlp-proto-http``; graceful fallback
         to JSONL if the package is not installed).
      2. ``OTEL_EXPORTER=console`` → ConsoleSpanExporter (built-in, no extra deps;
         useful in CI for inline span inspection).
      3. Neither set → local JSONL at
         ``<project_root>/.harness/traces/agent_trajectory.jsonl`` (default,
         zero-dependency, backward-compatible).

    Set env vars *before* the first harness_cli.py invocation — the tracer provider
    is initialised once per process and the exporter cannot be changed mid-run.
    """
    global _HARNESS_TRACER_INITIALIZED
    if _HARNESS_TRACER_INITIALIZED:
        return trace.get_tracer("harness_agent")

    resource = Resource.create({"service.name": "harness-methodology"})
    provider = TracerProvider(resource=resource)

    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    otel_mode = os.environ.get("OTEL_EXPORTER", "").strip().lower()

    if otlp_endpoint:
        # OTLP HTTP — requires: pip install opentelemetry-exporter-otlp-proto-http
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            otlp_exporter = OTLPSpanExporter(
                endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces"
            )
            provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        except ImportError:
            # Package not installed — fall back silently to local JSONL so the
            # gate pipeline is never blocked by a missing observability package.
            _add_jsonl_exporter(provider, project_root)
    elif otel_mode == "console":
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    else:
        _add_jsonl_exporter(provider, project_root)

    trace.set_tracer_provider(provider)
    _HARNESS_TRACER_INITIALIZED = True
    return trace.get_tracer("harness_agent")


def get_tracer() -> trace.Tracer:
    """Returns the globally configured tracer."""
    return trace.get_tracer("harness_agent")
