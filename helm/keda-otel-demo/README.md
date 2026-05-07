# KEDA OTEL Demo Helm Chart

This Helm chart deploys a complete KEDA + OpenTelemetry custom metrics autoscaling demo on Kubernetes/OpenShift.

## Prerequisites

- Kubernetes/OpenShift cluster
- Helm 3.x
- KEDA operator installed in the cluster
- kubectl/oc CLI configured

## Install KEDA (if not already installed)

```bash
kubectl apply -f https://github.com/kedacore/keda/releases/download/v2.15.0/keda-2.15.0.yaml
```

## Quick Start

### 1. Build the Order Processor Image

```bash
cd ../../app
podman build -t order-processor:latest .

# For OpenShift, push to internal registry:
# podman tag order-processor:latest image-registry.openshift-image-registry.svc:5000/keda-otel-demo/order-processor:latest
# podman push image-registry.openshift-image-registry.svc:5000/keda-otel-demo/order-processor:latest
```

### 2. Install the Chart

```bash
# From the helm directory
helm install keda-otel-demo ./keda-otel-demo

# Or install with custom values
helm install keda-otel-demo ./keda-otel-demo -f custom-values.yaml
```

### 3. Verify Installation

```bash
kubectl get pods -n keda-otel-demo -w
```

## Configuration

### Key Values

| Parameter | Description | Default |
|-----------|-------------|---------|
| `namespace` | Namespace for all resources | `keda-otel-demo` |
| `orderProcessor.image.repository` | Order processor image | `order-processor` |
| `orderProcessor.image.tag` | Order processor image tag | `latest` |
| `orderProcessor.env.processRate` | Orders processed per second per pod | `10` |
| `keda.scaledObject.threshold` | Pending orders threshold for scaling | `50` |
| `keda.scaledObject.minReplicaCount` | Minimum replicas | `1` |
| `keda.scaledObject.maxReplicaCount` | Maximum replicas | `10` |
| `loadGenerator.enabled` | Deploy load generator pod | `true` |

### Example: Adjust Scaling Behavior

Create a `custom-values.yaml`:

```yaml
keda:
  scaledObject:
    threshold: "100"
    minReplicaCount: 2
    maxReplicaCount: 20
    pollingInterval: 15
    cooldownPeriod: 30

orderProcessor:
  env:
    processRate: "20"
  resources:
    limits:
      memory: "512Mi"
      cpu: "500m"
```

Install with custom values:

```bash
helm install keda-otel-demo ./keda-otel-demo -f custom-values.yaml
```

## Usage

### Trigger Scaling

```bash
# Exec into load generator
kubectl exec -it -n keda-otel-demo load-generator -- sh

# Add 500 orders (triggers scaling)
curl -X POST http://order-processor:8080/add-orders \
  -H 'Content-Type: application/json' \
  -d '{"count": 500}'

# Check metrics
curl http://order-processor:8080/metrics-info
```

### Monitor Scaling

```bash
# Watch pods scale
kubectl get pods -n keda-otel-demo -w

# Check HPA
kubectl get hpa -n keda-otel-demo

# View Prometheus metrics
kubectl port-forward -n keda-otel-demo svc/prometheus 9090:9090
# Open http://localhost:9090 and query: pending_orders
```

## Upgrading

```bash
# Upgrade with new values
helm upgrade keda-otel-demo ./keda-otel-demo -f custom-values.yaml

# Force recreation of pods
helm upgrade keda-otel-demo ./keda-otel-demo --force
```

## Uninstalling

```bash
# Delete the release
helm uninstall keda-otel-demo

# Delete the namespace
kubectl delete namespace keda-otel-demo
```

## Chart Structure

```
keda-otel-demo/
├── Chart.yaml                          # Chart metadata
├── values.yaml                         # Default values
├── templates/
│   ├── namespace.yaml                  # Namespace
│   ├── otel-collector-configmap.yaml   # OTEL Collector config
│   ├── otel-collector-deployment.yaml  # OTEL Collector deployment
│   ├── otel-collector-service.yaml     # OTEL Collector service
│   ├── prometheus-configmap.yaml       # Prometheus config
│   ├── prometheus-deployment.yaml      # Prometheus deployment
│   ├── prometheus-service.yaml         # Prometheus service
│   ├── order-processor-deployment.yaml # Order processor deployment
│   ├── order-processor-service.yaml    # Order processor service
│   ├── keda-scaledobject.yaml         # KEDA ScaledObject
│   └── load-generator.yaml            # Load generator pod
└── README.md                           # This file
```

## Customization Examples

### Use External Prometheus

```yaml
prometheus:
  enabled: false  # Disable bundled Prometheus

keda:
  scaledObject:
    triggers:
      - type: prometheus
        serverAddress: http://my-prometheus.monitoring.svc:9090
        query: sum(pending_orders)
        threshold: "50"
```

### Change Image Registry

```yaml
orderProcessor:
  image:
    repository: quay.io/myorg/order-processor
    tag: "v1.2.3"
    pullPolicy: Always

otelCollector:
  image:
    repository: registry.example.com/otel/opentelemetry-collector
    tag: "0.96.0"
```

### Disable Load Generator

```yaml
loadGenerator:
  enabled: false
```

## Troubleshooting

### View Helm Release Status

```bash
helm status keda-otel-demo
helm get values keda-otel-demo
helm get manifest keda-otel-demo
```

### Check Template Rendering

```bash
helm template keda-otel-demo ./keda-otel-demo
helm template keda-otel-demo ./keda-otel-demo --debug
```

### Common Issues

**Pods not starting:**
```bash
kubectl describe pod -n keda-otel-demo <pod-name>
```

**ScaledObject not created:**
```bash
# Check if KEDA CRDs are installed
kubectl get crds | grep keda
```

**Metrics not flowing:**
```bash
# Check OTEL Collector logs
kubectl logs -n keda-otel-demo deployment/otel-collector

# Check Prometheus targets
kubectl port-forward -n keda-otel-demo svc/prometheus 9090:9090
# Visit http://localhost:9090/targets
```

## Development

### Lint Chart

```bash
helm lint ./keda-otel-demo
```

### Dry Run Install

```bash
helm install keda-otel-demo ./keda-otel-demo --dry-run --debug
```

### Package Chart

```bash
helm package ./keda-otel-demo
```

## License

Demo application for educational purposes.
