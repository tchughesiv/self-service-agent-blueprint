"""Outbox publisher metrics for observability.

Uses OpenTelemetry metrics API. When a MeterProvider is configured (e.g. OTLP
exporter via OTEL_EXPORTER_OTLP_ENDPOINT), these metrics are exported.
Otherwise no-ops via NoOpMeterProvider — negligible overhead.
"""

import functools
from typing import Callable, TypeVar

T = TypeVar("T")


def _suppress_metrics_errors(func: Callable[..., T]) -> Callable[..., T]:
    """Suppress exceptions in metrics recording (no-op if MeterProvider not configured)."""

    @functools.wraps(func)
    def wrapper(*args: object, **kwargs: object) -> T | None:
        try:
            return func(*args, **kwargs)
        except Exception:  # noqa: BLE001
            return None

    return wrapper  # type: ignore[return-value]


try:
    from opentelemetry import metrics
    from opentelemetry.metrics import Counter, Histogram

    _meter = metrics.get_meter(
        "integration-dispatcher.outbox",
        version="0.1.0",
    )
    _published_total: Counter = _meter.create_counter(
        name="integration_dispatcher_outbox_published_total",
        description="Outbox rows successfully published to broker",
        unit="1",
    )
    _failed_total: Counter = _meter.create_counter(
        name="integration_dispatcher_outbox_failed_total",
        description="Outbox rows that failed to publish (retry or exhausted)",
        unit="1",
    )
    _publish_duration_seconds: Histogram = _meter.create_histogram(
        name="integration_dispatcher_outbox_publish_duration_seconds",
        description="Time to process one outbox batch",
        unit="s",
    )
except Exception:  # noqa: BLE001
    _published_total = None
    _failed_total = None
    _publish_duration_seconds = None


@_suppress_metrics_errors
def record_published(count: int) -> None:
    """Record successfully published outbox rows."""
    if _published_total is not None and count > 0:
        _published_total.add(count)


@_suppress_metrics_errors
def record_failed(count: int) -> None:
    """Record failed outbox publish attempts."""
    if _failed_total is not None and count > 0:
        _failed_total.add(count)


@_suppress_metrics_errors
def record_batch_duration(seconds: float) -> None:
    """Record time to process one outbox batch."""
    if _publish_duration_seconds is not None:
        _publish_duration_seconds.record(seconds)
