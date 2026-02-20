from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider


def setup_otel():
    trace.set_tracer_provider(TracerProvider())
    return trace.get_tracer('elastic-agent-builder')
