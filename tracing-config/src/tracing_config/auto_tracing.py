import os
import typing

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.semconv.attributes import service_attributes
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

OTEL_EXPORTER_OTLP_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"


def tracingIsActive() -> bool:
    return bool(os.environ.get(OTEL_EXPORTER_OTLP_ENDPOINT))


def run(service_name: str, logger: typing.Any) -> None:
    otel_exporter_endpoint = os.environ.get(OTEL_EXPORTER_OTLP_ENDPOINT)
    if not otel_exporter_endpoint:
        logger.info("OTEL exporter endpoint not provided -- skip auto tracing config.")
        return

    logger.info(
        "Export tracing and metrics",
        otel_exporter_endpoint=otel_exporter_endpoint,
    )
    # Set up the tracer provider with service name
    resource = Resource(attributes={service_attributes.SERVICE_NAME: service_name})
    trace.set_tracer_provider(TracerProvider(resource=resource))

    # Set up the OTLP trace exporter
    otlp_trace_exporter = OTLPSpanExporter(
        endpoint=f"{otel_exporter_endpoint}/v1/traces",
    )

    # Set up the span processor
    span_processor = BatchSpanProcessor(otlp_trace_exporter)
    trace.get_tracer_provider().add_span_processor(span_processor)

    # Set up MeterProvider for session serialization metrics (lock timeouts, reclaims, etc.)
    otlp_metric_exporter = OTLPMetricExporter(
        endpoint=f"{otel_exporter_endpoint}/v1/metrics",
    )
    metric_reader = PeriodicExportingMetricReader(
        exporter=otlp_metric_exporter,
        export_interval_millis=60000,  # Export every 60 seconds
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    # Set up instrumentations
    HTTPXClientInstrumentor().instrument()

    # Set up propagator
    set_global_textmap(TraceContextTextMapPropagator())
