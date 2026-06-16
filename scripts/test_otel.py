import os
import time
from src.telemetry import pipeline_span, _instrument

os.environ["OTEL_ENABLED"] = "1"
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4318" # fake

with pipeline_span("test_run"):
    print("Inside span")
    time.sleep(1)

print("Done")
