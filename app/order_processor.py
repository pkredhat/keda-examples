#!/usr/bin/env python3
import os
import time
import threading
import queue
import random
from flask import Flask, request, jsonify

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.resources import Resource

app = Flask(__name__)

OTEL_COLLECTOR_ENDPOINT = os.getenv("OTEL_COLLECTOR_ENDPOINT", "otel-collector:4317")
PROCESS_RATE = float(os.getenv("PROCESS_RATE", "10"))
PROCESS_TIME = 1.0 / PROCESS_RATE

order_queue = queue.Queue()

resource = Resource.create({"service.name": "order-processor"})

otlp_exporter = OTLPMetricExporter(
    endpoint=OTEL_COLLECTOR_ENDPOINT,
    insecure=True
)

reader = PeriodicExportingMetricReader(otlp_exporter, export_interval_millis=5000)
provider = MeterProvider(resource=resource, metric_readers=[reader])
metrics.set_meter_provider(provider)

meter = metrics.get_meter(__name__)

def get_queue_size(options):
    yield metrics.Observation(order_queue.qsize())

pending_orders_gauge = meter.create_observable_gauge(
    name="pending_orders",
    description="Number of pending orders in the queue",
    unit="orders",
    callbacks=[get_queue_size]
)

processed_orders_counter = meter.create_counter(
    name="processed_orders_total",
    description="Total number of orders processed",
    unit="orders"
)

def process_orders():
    """Worker thread that processes orders from the queue"""
    while True:
        try:
            order = order_queue.get(timeout=1)
            time.sleep(PROCESS_TIME)
            processed_orders_counter.add(1)
            print(f"Processed order {order['id']}, queue size: {order_queue.qsize()}")
            order_queue.task_done()
        except queue.Empty:
            continue

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy", "pending_orders": order_queue.qsize()}), 200

@app.route('/add-orders', methods=['POST'])
def add_orders():
    """Add orders to the processing queue"""
    data = request.get_json() or {}
    count = data.get('count', 1)

    for i in range(count):
        order = {
            'id': f"order-{random.randint(1000, 9999)}-{int(time.time())}",
            'timestamp': time.time()
        }
        order_queue.put(order)

    return jsonify({
        "added": count,
        "pending_orders": order_queue.qsize()
    }), 200

@app.route('/clear-orders', methods=['POST'])
def clear_orders():
    """Clear all pending orders from the queue"""
    cleared = 0
    while not order_queue.empty():
        try:
            order_queue.get_nowait()
            cleared += 1
        except queue.Empty:
            break

    return jsonify({
        "cleared": cleared,
        "pending_orders": order_queue.qsize()
    }), 200

@app.route('/metrics-info', methods=['GET'])
def metrics_info():
    """Return current metrics for debugging"""
    return jsonify({
        "pending_orders": order_queue.qsize(),
        "otel_endpoint": OTEL_COLLECTOR_ENDPOINT,
        "process_rate": PROCESS_RATE
    }), 200

if __name__ == '__main__':
    worker_thread = threading.Thread(target=process_orders, daemon=True)
    worker_thread.start()

    print(f"Starting order processor...")
    print(f"OTEL Collector endpoint: {OTEL_COLLECTOR_ENDPOINT}")
    print(f"Process rate: {PROCESS_RATE} orders/sec")

    app.run(host='0.0.0.0', port=8080)
