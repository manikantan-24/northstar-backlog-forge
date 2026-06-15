"""OpenTelemetry instrumentation — traces + metrics for the health dashboard.

Emits:
  Traces → Tempo   : one span per stage, full pipeline root span
  Metrics → Mimir  : counters + histograms for the health dashboard

Configuration (env vars):
    OTEL_ENABLED                   set to "1" to enable
    OTEL_SERVICE_NAME              default: "backlog-synthesizer"
    OTEL_EXPORTER_OTLP_ENDPOINT    Grafana Cloud endpoint (without /v1/traces)
    OTEL_EXPORTER_OTLP_HEADERS     Authorization=Basic <base64(instanceId:apiKey)>

Metrics emitted (visible in Grafana dashboard):
    backlog_syntheses_total        Counter — total runs {status: ok|error}
    backlog_synthesis_duration_seconds  Histogram — end-to-end latency
    backlog_synthesis_cost_usd     Histogram — per-run USD cost
    backlog_active_synthesis       UpDownCounter — currently running pipelines
    backlog_tokens_total           Counter — LLM tokens consumed {stage, type}
    backlog_llm_errors_total       Counter — LLM API errors {provider}
    stage_duration_seconds         Histogram — per-stage latency {stage, model}
    guardrail_findings_total       Counter — guardrail issues {severity}
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Any, Generator

_ENABLED = os.environ.get("OTEL_ENABLED", "").strip() == "1"
_tracer         = None
_meter          = None
_meter_provider = None


def _parse_headers() -> dict[str, str]:
    headers_raw = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "").strip()
    headers: dict[str, str] = {}
    if not headers_raw:
        return headers
    for part in headers_raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            headers[k.strip()] = v.strip()
    return headers


def _get_tracer_and_meter():
    global _tracer, _meter, _meter_provider
    if _tracer is not None:
        return _tracer, _meter
    try:
        from opentelemetry import trace, metrics
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({
            "service.name": os.environ.get("OTEL_SERVICE_NAME", "backlog-synthesizer"),
            "service.version": "1.0.0",
        })

        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        headers  = _parse_headers()

        # ── Traces ────────────────────────────────────────────────────────────
        trace_provider = TracerProvider(resource=resource)
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                trace_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
                    endpoint=endpoint.rstrip("/") + "/v1/traces",
                    headers=headers or None,
                )))
            except ImportError:
                trace_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        else:
            trace_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

        trace.set_tracer_provider(trace_provider)
        _tracer = trace.get_tracer("backlog_synthesizer.pipeline")

        # ── Metrics ───────────────────────────────────────────────────────────
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
                metric_reader = PeriodicExportingMetricReader(
                    OTLPMetricExporter(
                        endpoint=endpoint.rstrip("/") + "/v1/metrics",
                        headers=headers or None,
                    ),
                    export_interval_millis=15_000,   # push every 15s
                )
            except ImportError:
                metric_reader = PeriodicExportingMetricReader(ConsoleMetricExporter())
        else:
            metric_reader = PeriodicExportingMetricReader(ConsoleMetricExporter())

        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        metrics.set_meter_provider(meter_provider)
        _meter_provider = meter_provider
        _meter = metrics.get_meter("backlog_synthesizer.pipeline")

    except ImportError:
        _tracer = None
        _meter  = None

    return _tracer, _meter


# ── Metric instrument cache ────────────────────────────────────────────────────
_instruments: dict[str, Any] = {}


def _instrument(name: str, kind: str, description: str, unit: str = "1") -> Any:
    """Get or create an OTel metric instrument (cached)."""
    if not _ENABLED:
        return None
    _, meter = _get_tracer_and_meter()
    if meter is None:
        return None
    key = f"{kind}:{name}"
    if key not in _instruments:
        if kind == "counter":
            _instruments[key] = meter.create_counter(name, description=description, unit=unit)
        elif kind == "updowncounter":
            _instruments[key] = meter.create_up_down_counter(name, description=description, unit=unit)
        elif kind == "histogram":
            _instruments[key] = meter.create_histogram(name, description=description, unit=unit)
    return _instruments.get(key)


# ── Span helpers ───────────────────────────────────────────────────────────────

class _NoopSpan:
    def set_attribute(self, *a, **kw): pass
    def set_status(self, *a, **kw): pass
    def record_exception(self, *a, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass


@contextmanager
def pipeline_span(
    run_id: str,
    model_summary: str = "",
    preset: str = "",
) -> Generator[Any, None, None]:
    """Root span + pipeline_runs_total counter for one full run."""
    if not _ENABLED:
        yield _NoopSpan()
        return

    tracer, _ = _get_tracer_and_meter()
    if tracer is None:
        yield _NoopSpan()
        return

    t0 = time.perf_counter()
    failed = False
    with tracer.start_as_current_span("pipeline.run") as span:
        span.set_attribute("run.id", run_id)
        span.set_attribute("run.model_summary", model_summary)
        span.set_attribute("run.preset", preset)
        try:
            yield span
        except Exception as exc:
            failed = True
            span.record_exception(exc)
            raise
        finally:
            elapsed = time.perf_counter() - t0
            status = "error" if failed else "ok"
            counter = _instrument("backlog_syntheses_total", "counter",
                                  "Total number of pipeline executions")
            if counter:
                counter.add(1, {"preset": preset, "status": status})
            hist = _instrument("backlog_synthesis_duration_seconds", "histogram",
                               "End-to-end pipeline run duration", unit="s")
            if hist:
                hist.record(elapsed, {"preset": preset, "status": status})


@contextmanager
def stage_span(
    stage_name: str,
    model: str = "",
    input_chars: int = 0,
) -> Generator[Any, None, None]:
    """Span + stage_duration_seconds histogram for one agent stage."""
    if not _ENABLED:
        yield _NoopSpan()
        return

    tracer, _ = _get_tracer_and_meter()
    if tracer is None:
        yield _NoopSpan()
        return

    t0 = time.perf_counter()
    with tracer.start_as_current_span(f"stage.{stage_name}") as span:
        span.set_attribute("stage.name", stage_name)
        span.set_attribute("stage.model", model)
        span.set_attribute("stage.input_chars", input_chars)
        try:
            yield span
        finally:
            elapsed = time.perf_counter() - t0
            hist = _instrument("stage_duration_seconds", "histogram",
                               "Per-stage agent duration", unit="s")
            if hist:
                hist.record(elapsed, {"stage": stage_name, "model": model})


def record_stage_tokens(
    span: Any,
    input_tokens: int,
    output_tokens: int,
    stage: str = "",
) -> None:
    """Attach token counts to span + increment stage_tokens_total counter.

    Pass `stage` explicitly — avoids accessing the private `span._name` attribute
    which is not part of the OTel SDK public API.
    """
    if not _ENABLED:
        return
    try:
        span.set_attribute("llm.input_tokens", input_tokens)
        span.set_attribute("llm.output_tokens", output_tokens)
        span.set_attribute("llm.total_tokens", input_tokens + output_tokens)
    except Exception:  # noqa: BLE001
        pass

    counter = _instrument("backlog_tokens_total", "counter",
                          "Total LLM tokens consumed across all stages")
    if counter:
        counter.add(input_tokens,  {"type": "input",  "stage": stage})
        counter.add(output_tokens, {"type": "output", "stage": stage})


@contextmanager
def child_span(name: str, **attributes) -> Generator[Any, None, None]:
    """Create a child span under whatever span is currently active.

    Use this in tools and agents to add nested detail to the stage spans.
    Automatically no-ops when OTEL is disabled or the SDK is not installed,
    so tools never break in environments without telemetry configured.

    Example:
        with child_span("llm.call", model="claude-sonnet-4-5") as span:
            response = client.messages.create(...)
            span.set_attribute("llm.tokens_out", response.usage.output_tokens)
    """
    if not _ENABLED:
        yield _NoopSpan()
        return
    tracer, _ = _get_tracer_and_meter()
    if tracer is None:
        yield _NoopSpan()
        return
    with tracer.start_as_current_span(name) as span:
        for k, v in attributes.items():
            try:
                span.set_attribute(k, v)
            except Exception:  # noqa: BLE001
                pass
        try:
            yield span
        except Exception as exc:  # noqa: BLE001
            span.record_exception(exc)
            raise


@contextmanager
def pipeline_node_span(node_name: str, run_id: str = "", **attributes: Any) -> Generator[Any, None, None]:
    """OTel span for a single LangGraph pipeline node.

    No-op when OTEL_ENABLED is not "1".
    Records exception and marks span ERROR so traces show exactly which node failed.
    """
    if not _ENABLED:
        yield _NoopSpan()
        return

    tracer, _ = _get_tracer_and_meter()
    if tracer is None:
        yield _NoopSpan()
        return

    try:
        from opentelemetry.trace import StatusCode
    except Exception:  # noqa: BLE001
        yield _NoopSpan()
        return

    with tracer.start_as_current_span(f"pipeline.node.{node_name}") as span:
        span.set_attribute("pipeline.node", node_name)
        if run_id:
            span.set_attribute("pipeline.run_id", run_id)
        for k, v in attributes.items():
            try:
                span.set_attribute(k, v)
            except Exception:  # noqa: BLE001
                pass
        try:
            yield span
            span.set_status(StatusCode.OK)
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(StatusCode.ERROR, description=str(exc)[:200])
            raise


def inc_active_synthesis() -> None:
    """Increment backlog_active_synthesis gauge (call at run start)."""
    if not _ENABLED:
        return
    c = _instrument("backlog_active_synthesis", "updowncounter",
                    "Number of pipeline runs currently in progress")
    if c:
        c.add(1)


def dec_active_synthesis() -> None:
    """Decrement backlog_active_synthesis gauge (call at run end/error)."""
    if not _ENABLED:
        return
    c = _instrument("backlog_active_synthesis", "updowncounter",
                    "Number of pipeline runs currently in progress")
    if c:
        c.add(-1)


def record_synthesis_complete(
    run_id: str = "",
    preset: str = "",
    elapsed_seconds: float = 0.0,
    status: str = "ok",
) -> None:
    """Emit backlog_syntheses_total counter + backlog_synthesis_duration_seconds histogram.

    Call once per run, just before dec_active_synthesis. This replaces the
    pipeline_span context-manager approach which required wrapping the entire
    900-line orchestrator run() method.
    """
    if not _ENABLED:
        return
    attrs = {"preset": preset, "status": status}
    counter = _instrument("backlog_syntheses_total", "counter",
                          "Total number of pipeline executions")
    if counter:
        counter.add(1, attrs)
    if elapsed_seconds > 0:
        hist = _instrument("backlog_synthesis_duration_seconds", "histogram",
                           "End-to-end pipeline run duration", unit="s")
        if hist:
            hist.record(elapsed_seconds, attrs)


def record_pipeline_cost(cost_usd: float, preset: str = "") -> None:
    """Record per-run cost to backlog_synthesis_cost_usd histogram."""
    if not _ENABLED or cost_usd <= 0:
        return
    hist = _instrument("backlog_synthesis_cost_usd", "histogram",
                       "Per-run estimated LLM cost in USD", unit="USD")
    if hist:
        hist.record(cost_usd, {"preset": preset})


def flush_metrics(timeout_ms: int = 5_000) -> None:
    """Force-flush the OTel MeterProvider so metrics are shipped before scale-to-zero.

    Call once at the end of every synthesis run. Without this, the periodic
    exporter (15s interval) may not fire before Azure Container Apps idles the
    container, leaving the last run invisible in Grafana.
    """
    if not _ENABLED or _meter_provider is None:
        return
    try:
        _meter_provider.force_flush(timeout_millis=timeout_ms)
    except Exception:  # noqa: BLE001
        pass


def record_llm_error(provider: str = "") -> None:
    """Increment backlog_llm_errors_total (call on LLM API errors)."""
    if not _ENABLED:
        return
    c = _instrument("backlog_llm_errors_total", "counter",
                    "Total LLM API errors by provider")
    if c:
        c.add(1, {"provider": provider or "unknown"})


def record_guardrail_findings(span: Any, error: int, warn: int, info: int) -> None:
    """Attach guardrail counts to span + increment guardrail_findings_total."""
    if not _ENABLED:
        return
    try:
        span.set_attribute("guardrails.error_count", error)
        span.set_attribute("guardrails.warn_count", warn)
        span.set_attribute("guardrails.info_count", info)
    except Exception:  # noqa: BLE001
        pass

    counter = _instrument("guardrail_findings_total", "counter",
                          "Guardrail findings by severity")
    if counter:
        if error: counter.add(error, {"severity": "error"})
        if warn:  counter.add(warn,  {"severity": "warn"})
        if info:  counter.add(info,  {"severity": "info"})
