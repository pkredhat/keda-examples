# KEDA + OpenTelemetry Custom Metrics Demo

This demo showcases KEDA autoscaling based on custom business metrics emitted via OpenTelemetry. The scenario simulates an order processing service that scales horizontally based on the number of pending orders.

## Workflow Summary

This demo showcases how KEDA can autoscale workloads based on custom business metrics using OpenTelemetry. The **order-processor** application simulates a queue-based system that processes orders at 10 orders/sec per pod. Each pod is instrumented with the OpenTelemetry Python SDK, which emits a custom gauge metric (`pending_orders`) representing the current queue depth. These metrics are sent via OTLP/gRPC to the **OTEL Collector**, which transforms and exports them in Prometheus format on port 8889. **Prometheus** then scrapes this endpoint every 15 seconds, storing the time-series data for the `pending_orders` metric.

KEDA monitors this metric through its **ScaledObject** resource, which is configured with a Prometheus trigger that queries `sum(pending_orders)` with a threshold of 50. When the pending orders exceed 50, KEDA creates a HorizontalPodAutoscaler (HPA) that scales the order-processor deployment from 1 to a maximum of 10 replicas. As more workers come online, the processing capacity increases (workers × 10 orders/sec), draining the backlog faster. Once the pending orders drop below the threshold and remain low through the cooldown period (60s), KEDA scales the deployment back down. This demonstrates event-driven autoscaling based on domain-specific metrics rather than generic infrastructure metrics like CPU or memory, enabling smarter capacity management aligned with actual business workload demands.

## Architecture

```
┌─────────────────┐
│ Order Processor │ (Deployment - scales based on pending_orders)
│   (Python App)  │
└────────┬────────┘
         │ OTLP/gRPC
         │ (metrics: pending_orders)
         ▼
┌─────────────────┐
│ OTEL Collector  │
│ (Port 4317)     │
└────────┬────────┘
         │ Prometheus
         │ Exporter
         │ (Port 8889)
         ▼
┌─────────────────┐
│  Prometheus     │ ◄───── Scrapes metrics
│  (Port 9090)    │        every 15s
└────────┬────────┘
         │
         │ PromQL Query:
         │ sum(pending_orders)
         ▼
┌─────────────────┐
│  KEDA           │ ◄───── Scales when
│  ScaledObject   │        pending_orders > 50
└─────────────────┘
```

## Components

1. **Order Processor** - Python Flask app with OTEL instrumentation
   - Emits `pending_orders` gauge metric
   - Processes orders at configurable rate (default: 10 orders/sec per pod)
   - HTTP endpoints for health checks and adding orders

2. **OTEL Collector** - Receives OTLP metrics and exports to Prometheus format

3. **Prometheus** - Time-series database storing metrics

4. **KEDA ScaledObject** - Autoscaler that queries Prometheus and scales the deployment

5. **Load Generator** - Pod with curl for triggering scaling events

6. **Dashboard** - Vue.js web application for visualizing the demo
   - Real-time metrics display (pending orders, replicas, processing rate)
   - Interactive load generation buttons
   - Drain estimate calculator
   - Animated pod grid visualization
   - KEDA autoscaling toggle (enable/disable ScaledObject)
   - Observability stack monitoring (Prometheus, OTEL Collector)
   - Full-width pending orders activity chart
   - HPA status display

## Prerequisites

- OpenShift cluster (logged in as cluster-admin)
- KEDA operator installed
- Podman for building images
- Helm 3.x

## Quick Start

See [helm/keda-otel-demo/README.md](helm/keda-otel-demo/README.md) for detailed Helm instructions.

### 1. Build and Push Images

```bash
# Get the OpenShift registry route
REGISTRY=$(oc get route -n openshift-image-registry default-route -o jsonpath='{.spec.host}')

# Login to OpenShift registry
podman login -u $(oc whoami) -p $(oc whoami -t) $REGISTRY --tls-verify=false

# Build and push order processor
cd app
podman build --platform linux/amd64 -t $REGISTRY/keda-otel-demo/order-processor:latest .
podman push $REGISTRY/keda-otel-demo/order-processor:latest --tls-verify=false

# Build and push dashboard (optional)
cd ../dashboard
podman build --platform linux/amd64 -t $REGISTRY/keda-otel-demo/dashboard:latest .
podman push $REGISTRY/keda-otel-demo/dashboard:latest --tls-verify=false
```

### 2. Install with Helm

```bash
# Create override values file
cat > override-values.yaml <<EOF
orderProcessor:
  image:
    repository: image-registry.openshift-image-registry.svc:5000/keda-otel-demo/order-processor
    pullPolicy: Always

keda:
  scaledObject:
    triggers:
      - type: prometheus
        serverAddress: http://prometheus.keda-otel-demo.svc:9090
        query: sum(pending_orders)
        threshold: "50"
EOF

# Install
helm install keda-otel-demo ./helm/keda-otel-demo -f override-values.yaml
```

## Customization

### Adjust Scaling Behavior

Edit `override-values.yaml`:

```yaml
keda:
  scaledObject:
    threshold: "100"        # Scale when pending_orders > 100
    minReplicaCount: 2      # Minimum replicas
    maxReplicaCount: 20     # Maximum replicas
    pollingInterval: 15     # Check Prometheus every 15s
    cooldownPeriod: 30      # Wait 30s before scale down
```

### Adjust Processing Rate

```yaml
orderProcessor:
  env:
    processRate: "20"  # Process 20 orders/sec per pod
```

Then upgrade:
```bash
helm upgrade keda-otel-demo ./helm/keda-otel-demo -f override-values.yaml
```

## Verifying Deployment

### Check All Components

```bash
# Check all pods are running
oc get pods -n keda-otel-demo

# Expected output:
# NAME                                READY   STATUS    RESTARTS   AGE
# load-generator                      1/1     Running   0          2m
# otel-collector-xxxxx                1/1     Running   0          2m
# order-processor-xxxxx               1/1     Running   0          2m
# prometheus-xxxxx                    1/1     Running   0          2m
```

### Verify KEDA ScaledObject

```bash
# Check ScaledObject status
oc get scaledobject -n keda-otel-demo

# Check HPA created by KEDA
oc get hpa -n keda-otel-demo
```

### Verify Metrics Flow

```bash
# Check OTEL Collector is receiving metrics
oc logs -n keda-otel-demo deployment/otel-collector

# Port-forward to Prometheus (optional)
oc port-forward -n keda-otel-demo svc/prometheus 9090:9090

# Open browser to http://localhost:9090
# Query: pending_orders
```

## Accessing the Dashboard

If you enabled the dashboard in your override-values.yaml:

```yaml
dashboard:
  enabled: true
  image:
    repository: image-registry.openshift-image-registry.svc:5000/keda-otel-demo/dashboard
    pullPolicy: Always
```

Get the dashboard URL:

```bash
oc get route -n keda-otel-demo dashboard -o jsonpath='https://{.spec.host}'
```

The dashboard provides:
- **Generate Load**: Buttons to add 100/300/500/1000 orders or clear the queue
- **Live Metrics**: Real-time pending orders, worker count, capacity, and drain estimate
- **KEDA Autoscaling**: Toggle to enable/disable the ScaledObject
- **Observability Stack**: OTEL Collector and Prometheus health monitoring
- **Worker Pods**: Animated pod grid with status details and HPA information
- **Pending Orders Chart**: Full-width time-series visualization
- **KEDA Events Log**: Recent scaling events and status changes

## Testing

### Method 1: Using the Dashboard (Recommended)

Open the dashboard URL and click the order buttons to trigger scaling events. Watch the animated pod grid, drain estimate, and pending orders chart update in real-time.

### Method 2: Using oc exec

```bash
# Generate 100 orders (won't trigger scaling)
oc exec -n keda-otel-demo load-generator -- curl -X POST http://order-processor:8080/add-orders -H 'Content-Type: application/json' -d '{"count": 100}'

# Generate 500 orders (will trigger scaling to ~10 pods)
oc exec -n keda-otel-demo load-generator -- curl -X POST http://order-processor:8080/add-orders -H 'Content-Type: application/json' -d '{"count": 500}'

# Check current queue size
oc exec -n keda-otel-demo load-generator -- curl -s http://order-processor:8080/metrics-info
```

### Method 3: Port-forward and Use Local curl

```bash
oc port-forward -n keda-otel-demo svc/order-processor 8080:8080

# In another terminal
curl -X POST http://localhost:8080/add-orders -H 'Content-Type: application/json' -d '{"count": 500}'
```

### Method 4: Continuous Load

```bash
# Run continuous load
oc exec -n keda-otel-demo load-generator -- sh -c '
while true; do 
  curl -X POST http://order-processor:8080/add-orders -H "Content-Type: application/json" -d "{\"count\": 50}"
  sleep 5
done
'
```

Stop with `Ctrl+C`.

## Observing Scaling

### Watch Pods Scale

```bash
oc get pods -n keda-otel-demo -w
```

You should see:
1. Initially 1 replica of order-processor
2. After adding many orders, replicas scale up (max 10)
3. After load stops, replicas scale down after cooldown period (60s)

### Watch HPA

```bash
oc get hpa -n keda-otel-demo -w
```

### Check KEDA Operator Logs

```bash
oc logs -n keda deployment/keda-operator -f
```

### Query Prometheus

```bash
oc port-forward -n keda-otel-demo svc/prometheus 9090:9090
```

Open http://localhost:9090 and run queries:
- `pending_orders` - See current queue size
- `rate(processed_orders_total[1m])` - Processing rate
- Compare pending_orders with replica count

## Understanding the Scaling Behavior

**KEDA Configuration:**
- **Threshold:** 50 pending orders
- **Min Replicas:** 1
- **Max Replicas:** 10
- **Polling Interval:** 30s (KEDA checks Prometheus every 30s)
- **Cooldown Period:** 60s (wait before scaling down)

**Scaling Formula:**
```
desiredReplicas = ceil(sum(pending_orders) / threshold)
desiredReplicas = ceil(sum(pending_orders) / 50)
```

**Examples:**
- 0-50 pending orders → 1 replica
- 51-100 pending orders → 2 replicas
- 101-150 pending orders → 3 replicas
- 450-500 pending orders → 10 replicas (capped at max)

**Processing Capacity:**
- Each pod processes 10 orders/second
- 1 pod = 600 orders/minute
- 10 pods = 6000 orders/minute

## Troubleshooting

### Pods Not Scaling

1. **Check ScaledObject:**
   ```bash
   oc describe scaledobject -n keda-otel-demo order-processor-scaler
   ```

2. **Verify Prometheus has metrics:**
   ```bash
   oc port-forward -n keda-otel-demo svc/prometheus 9090:9090
   # Query: pending_orders
   ```

3. **Check KEDA operator logs:**
   ```bash
   oc logs -n keda deployment/keda-operator
   ```

4. **Verify OTEL Collector is exporting:**
   ```bash
   oc logs -n keda-otel-demo deployment/otel-collector
   # Should see metric exports
   ```

### Metrics Not Appearing in Prometheus

1. **Check OTEL Collector logs:**
   ```bash
   oc logs -n keda-otel-demo deployment/otel-collector
   ```

2. **Check order processor is sending metrics:**
   ```bash
   oc logs -n keda-otel-demo deployment/order-processor
   ```

3. **Verify OTEL Collector endpoint is accessible:**
   ```bash
   oc exec -n keda-otel-demo deployment/order-processor -- nc -zv otel-collector 4317
   ```

### ScaledObject Not Found

KEDA might not be installed or running:
```bash
oc get pods -n keda
oc get crds | grep keda
```

## Cleanup

```bash
# Uninstall Helm release
helm uninstall keda-otel-demo -n keda-kafka-demo

# Delete namespace
oc delete namespace keda-otel-demo
```

## Architecture Highlights

**Why OpenTelemetry?**
- Vendor-neutral observability standard
- Rich SDK ecosystem
- Flexible metric export (can send to multiple backends)
- Industry standard for cloud-native apps

**Why Prometheus?**
- KEDA has native Prometheus scaler
- PromQL provides powerful query capabilities
- Widely adopted in Kubernetes ecosystem

**Why Custom Metrics?**
- Scales based on business logic, not just infrastructure metrics
- More meaningful autoscaling decisions
- Examples: queue depth, active users, transaction rate, etc.

**Key Benefits:**
- Autoscale on what matters to your application
- Decouple metric collection (OTEL) from scaling logic (KEDA)
- Observable system with metrics, logs, and traces potential

## Next Steps

1. Add distributed tracing via OTEL
2. Export metrics to additional backends (Grafana, Datadog, etc.)
3. Implement custom PromQL queries for complex scaling logic
4. Add resource-based scaling alongside metric-based scaling
5. Implement scale-to-zero for idle periods
