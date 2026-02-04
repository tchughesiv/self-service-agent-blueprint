"""Session serialization metrics for observability.

Uses OpenTelemetry metrics API. When a MeterProvider is configured (e.g. OTLP
exporter), these metrics are exported. Otherwise no-ops via default NoOpMeterProvider.
"""

try:
    from opentelemetry import metrics
    from opentelemetry.metrics import Counter, Histogram

    _meter = metrics.get_meter(
        "request-manager.session-serialization",
        version="0.1.0",
    )
    _lock_acquire_duration: Histogram = _meter.create_histogram(
        name="request_manager_session_lock_acquire_duration_seconds",
        description="Time spent waiting to acquire session lock",
        unit="s",
    )
    _lock_timeout_total: Counter = _meter.create_counter(
        name="request_manager_session_lock_timeout_total",
        description="Total session lock timeouts (503)",
    )
    _request_log_creation_failure_total: Counter = _meter.create_counter(
        name="request_manager_request_log_creation_failure_total",
        description="Total RequestLog creation failures (503)",
    )
    _reclaim_total: Counter = _meter.create_counter(
        name="request_manager_reclaim_total",
        description="Total stuck processing requests reclaimed",
        unit="1",
    )
    _reclaim_on_demand_total: Counter = _meter.create_counter(
        name="request_manager_reclaim_on_demand_total",
        description="Stuck requests reclaimed on-demand (before dequeue)",
        unit="1",
    )
    _reclaim_background_total: Counter = _meter.create_counter(
        name="request_manager_reclaim_background_total",
        description="Stuck requests reclaimed by background task",
        unit="1",
    )
except Exception:  # noqa: BLE001
    _lock_acquire_duration = None
    _lock_timeout_total = None
    _request_log_creation_failure_total = None
    _reclaim_total = None
    _reclaim_on_demand_total = None
    _reclaim_background_total = None


def record_lock_acquire_duration(seconds: float) -> None:
    """Record lock acquisition duration."""
    if _lock_acquire_duration is not None:
        try:
            _lock_acquire_duration.record(seconds)
        except Exception:  # noqa: BLE001
            pass


def record_lock_timeout() -> None:
    """Record session lock timeout (503)."""
    if _lock_timeout_total is not None:
        try:
            _lock_timeout_total.add(1)
        except Exception:  # noqa: BLE001
            pass


def record_request_log_creation_failure() -> None:
    """Record RequestLog creation failure (503)."""
    if _request_log_creation_failure_total is not None:
        try:
            _request_log_creation_failure_total.add(1)
        except Exception:  # noqa: BLE001
            pass


def record_reclaim_on_demand(count: int) -> None:
    """Record on-demand reclaim (before dequeue)."""
    if _reclaim_on_demand_total is not None and count > 0:
        try:
            _reclaim_on_demand_total.add(count)
            if _reclaim_total is not None:
                _reclaim_total.add(count)
        except Exception:  # noqa: BLE001
            pass


def record_reclaim_background(count: int) -> None:
    """Record background reclaim."""
    if _reclaim_background_total is not None and count > 0:
        try:
            _reclaim_background_total.add(count)
            if _reclaim_total is not None:
                _reclaim_total.add(count)
        except Exception:  # noqa: BLE001
            pass
