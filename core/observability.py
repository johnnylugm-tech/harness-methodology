import json
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


def init_tracer(project_root: Path) -> trace.Tracer:
    """Initializes and returns the OpenTelemetry tracer for Agentic Trajectory Logging."""
    global _HARNESS_TRACER_INITIALIZED
    if _HARNESS_TRACER_INITIALIZED:
        return trace.get_tracer("harness_agent")

    resource = Resource.create({"service.name": "harness-methodology"})
    provider = TracerProvider(resource=resource)

    # Export to a local JSONL file for offline time-travel debugging
    log_dir = project_root / ".harness" / "traces"
    file_exporter = JsonFileSpanExporter(log_dir)
    provider.add_span_processor(BatchSpanProcessor(file_exporter))

    trace.set_tracer_provider(provider)
    _HARNESS_TRACER_INITIALIZED = True
    return trace.get_tracer("harness_agent")

def get_tracer() -> trace.Tracer:
    """Returns the globally configured tracer."""
    return trace.get_tracer("harness_agent")
