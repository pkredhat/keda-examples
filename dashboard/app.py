from flask import Flask, jsonify, send_from_directory, request as freq
import requests
import os

app = Flask(__name__, static_folder='static')

NAMESPACE = os.getenv("NAMESPACE", "keda-otel-demo")
ORDER_PROCESSOR_URL = f"http://order-processor.{NAMESPACE}.svc:8080"
PROMETHEUS_URL = f"http://prometheus.{NAMESPACE}.svc:9090"
API = "https://kubernetes.default.svc"
TOKEN_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

def k8s_headers():
    token = open(TOKEN_FILE).read()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def k8s_get(path):
    return requests.get(f"{API}{path}", headers=k8s_headers(), verify=CA)

def k8s_post(path, data):
    return requests.post(f"{API}{path}", headers=k8s_headers(), json=data, verify=CA)

def k8s_delete(path):
    return requests.delete(f"{API}{path}", headers=k8s_headers(), verify=CA)

def query_prometheus(query):
    try:
        r = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={'query': query}, timeout=5)
        if r.status_code == 200:
            result = r.json()
            if result['status'] == 'success' and result['data']['result']:
                return float(result['data']['result'][0]['value'][1])
    except:
        pass
    return 0

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/metrics')
def get_metrics():
    try:
        # Get metrics from order processor
        processor_resp = requests.get(f"{ORDER_PROCESSOR_URL}/metrics-info", timeout=3)
        processor_data = processor_resp.json() if processor_resp.status_code == 200 else {}

        # Get metrics from Prometheus
        pending = query_prometheus('sum(pending_orders)')
        processed_total = query_prometheus('sum(processed_orders_total)')
        processing_rate = query_prometheus('rate(processed_orders_total[1m])')

        # Get replica count
        deploy_resp = k8s_get(f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/order-processor")
        replicas = 1
        if deploy_resp.status_code == 200:
            deploy_data = deploy_resp.json()
            replicas = deploy_data.get('status', {}).get('replicas', 1)

        return jsonify({
            'pending_orders': int(pending),
            'processed_total': int(processed_total),
            'processing_rate': round(processing_rate, 2),
            'replicas': replicas,
            'capacity': replicas * 10,  # 10 orders/sec per pod
            'process_rate': processor_data.get('process_rate', 10)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/orders', methods=['POST'])
def add_orders():
    try:
        data = freq.get_json()
        count = data.get('count', 100)

        resp = requests.post(
            f"{ORDER_PROCESSOR_URL}/add-orders",
            json={'count': count},
            timeout=5
        )

        if resp.status_code == 200:
            result = resp.json()
            return jsonify({
                'status': 'ok',
                'message': f'Added {count} orders',
                'pending_orders': result.get('pending_orders', 0)
            })
        else:
            return jsonify({'status': 'error', 'message': 'Failed to add orders'}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/orders/clear', methods=['POST'])
def clear_orders():
    try:
        resp = requests.post(
            f"{ORDER_PROCESSOR_URL}/clear-orders",
            timeout=5
        )

        if resp.status_code == 200:
            return jsonify({
                'status': 'ok',
                'message': 'Queue cleared'
            })
        else:
            return jsonify({'status': 'error', 'message': 'Failed to clear orders'}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/keda')
def get_keda_status():
    try:
        resp = k8s_get(f"/apis/keda.sh/v1alpha1/namespaces/{NAMESPACE}/scaledobjects")
        if resp.status_code == 200:
            data = resp.json()
            items = data.get('items', [])
            so = next((item for item in items if item['metadata']['name'] == 'order-processor-scaler'), None)

            if so:
                return jsonify({
                    'enabled': True,
                    'name': so['metadata']['name'],
                    'min': so['spec'].get('minReplicaCount', 1),
                    'max': so['spec'].get('maxReplicaCount', 10),
                    'triggers': so['spec'].get('triggers', [])
                })

        return jsonify({'enabled': False})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/keda', methods=['POST'])
def toggle_keda():
    try:
        data = freq.get_json()
        enable = data.get('enable', False)

        if enable:
            # Create ScaledObject
            scaled_object = {
                "apiVersion": "keda.sh/v1alpha1",
                "kind": "ScaledObject",
                "metadata": {
                    "name": "order-processor-scaler",
                    "namespace": NAMESPACE
                },
                "spec": {
                    "scaleTargetRef": {"name": "order-processor"},
                    "minReplicaCount": 1,
                    "maxReplicaCount": 10,
                    "pollingInterval": 30,
                    "cooldownPeriod": 60,
                    "triggers": [{
                        "type": "prometheus",
                        "metadata": {
                            "serverAddress": f"http://prometheus.{NAMESPACE}.svc:9090",
                            "query": "sum(pending_orders)",
                            "threshold": "50"
                        }
                    }]
                }
            }

            resp = k8s_post(f"/apis/keda.sh/v1alpha1/namespaces/{NAMESPACE}/scaledobjects", scaled_object)
            if resp.status_code in [200, 201]:
                return jsonify({'status': 'ok', 'message': 'KEDA autoscaling enabled'})
            else:
                return jsonify({'status': 'error', 'message': f'Failed: {resp.text}'}), 500
        else:
            # Delete ScaledObject
            resp = k8s_delete(f"/apis/keda.sh/v1alpha1/namespaces/{NAMESPACE}/scaledobjects/order-processor-scaler")
            if resp.status_code in [200, 404]:
                return jsonify({'status': 'ok', 'message': 'KEDA autoscaling disabled'})
            else:
                return jsonify({'status': 'error', 'message': f'Failed: {resp.text}'}), 500

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/pods')
def get_pods():
    try:
        resp = k8s_get(f"/api/v1/namespaces/{NAMESPACE}/pods?labelSelector=app=order-processor")
        if resp.status_code == 200:
            data = resp.json()
            pods = []
            for item in data.get('items', []):
                pods.append({
                    'name': item['metadata']['name'],
                    'status': item['status']['phase'],
                    'ready': sum(1 for c in item['status'].get('containerStatuses', []) if c.get('ready', False)),
                    'age': item['metadata']['creationTimestamp']
                })
            return jsonify({'pods': pods})
        return jsonify({'pods': []}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/events')
def get_events():
    try:
        # Get events from both namespaces
        events = []

        for ns in [NAMESPACE, 'openshift-keda']:
            resp = k8s_get(f"/api/v1/namespaces/{ns}/events")
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get('items', []):
                    if 'keda' in item.get('involvedObject', {}).get('name', '').lower() or \
                       'scaledobject' in item.get('involvedObject', {}).get('kind', '').lower():
                        events.append({
                            'type': item.get('type', 'Normal'),
                            'reason': item.get('reason', ''),
                            'message': item.get('message', ''),
                            'time': item.get('lastTimestamp') or item.get('firstTimestamp'),
                            'object': f"{item['involvedObject']['kind']}/{item['involvedObject']['name']}",
                            'namespace': ns
                        })

        # Sort by time descending
        events.sort(key=lambda x: x['time'], reverse=True)
        return jsonify({'events': events[:50]})  # Last 50 events
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/hpa')
def get_hpa():
    try:
        resp = k8s_get(f"/apis/autoscaling/v2/namespaces/{NAMESPACE}/horizontalpodautoscalers")
        if resp.status_code == 200:
            data = resp.json()
            hpas = []
            for item in data.get('items', []):
                hpas.append({
                    'name': item['metadata']['name'],
                    'target': f"{item['spec']['scaleTargetRef']['kind']}/{item['spec']['scaleTargetRef']['name']}",
                    'min': item['spec'].get('minReplicas', 1),
                    'max': item['spec'].get('maxReplicas', 10),
                    'current': item['status'].get('currentReplicas', 0),
                    'desired': item['status'].get('desiredReplicas', 0)
                })
            return jsonify({'hpas': hpas})
        return jsonify({'hpas': []}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/prometheus')
def get_prometheus_info():
    try:
        # Get Prometheus targets
        targets_resp = requests.get(f"{PROMETHEUS_URL}/api/v1/targets", timeout=5)
        targets = []
        if targets_resp.status_code == 200:
            data = targets_resp.json()
            for target in data.get('data', {}).get('activeTargets', []):
                targets.append({
                    'job': target.get('labels', {}).get('job', ''),
                    'instance': target.get('labels', {}).get('instance', ''),
                    'health': target.get('health', ''),
                    'lastScrape': target.get('lastScrape', '')
                })

        # Get key metrics
        metrics = {
            'pending_orders': query_prometheus('sum(pending_orders)'),
            'processed_total': query_prometheus('sum(processed_orders_total)'),
            'processing_rate': query_prometheus('rate(processed_orders_total[1m])'),
            'scrape_samples': query_prometheus('prometheus_tsdb_head_samples')
        }

        return jsonify({
            'targets': targets,
            'metrics': metrics,
            'url': PROMETHEUS_URL
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/otel')
def get_otel_info():
    try:
        # Get OTEL Collector pod
        resp = k8s_get(f"/api/v1/namespaces/{NAMESPACE}/pods?labelSelector=app=otel-collector")
        otel_status = {'status': 'Unknown', 'pod': None}

        if resp.status_code == 200:
            data = resp.json()
            if data.get('items'):
                pod = data['items'][0]
                otel_status = {
                    'status': pod['status']['phase'],
                    'pod': pod['metadata']['name'],
                    'ready': sum(1 for c in pod['status'].get('containerStatuses', []) if c.get('ready', False)),
                    'restarts': sum(c.get('restartCount', 0) for c in pod['status'].get('containerStatuses', []))
                }

        # Get OTEL Collector service
        svc_resp = k8s_get(f"/api/v1/namespaces/{NAMESPACE}/services/otel-collector")
        endpoints = {}
        if svc_resp.status_code == 200:
            svc = svc_resp.json()
            for port in svc['spec'].get('ports', []):
                endpoints[port['name']] = f"{port['port']}/{port.get('protocol', 'TCP')}"

        return jsonify({
            'status': otel_status,
            'endpoints': endpoints,
            'receiver': 'OTLP (gRPC: 4317, HTTP: 4318)',
            'exporter': 'Prometheus (8889)'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/scaledobject')
def get_scaledobject_details():
    try:
        resp = k8s_get(f"/apis/keda.sh/v1alpha1/namespaces/{NAMESPACE}/scaledobjects/order-processor-scaler")
        if resp.status_code == 200:
            so = resp.json()
            return jsonify({
                'exists': True,
                'name': so['metadata']['name'],
                'namespace': so['metadata']['namespace'],
                'scaleTarget': so['spec']['scaleTargetRef']['name'],
                'minReplicas': so['spec'].get('minReplicaCount', 1),
                'maxReplicas': so['spec'].get('maxReplicaCount', 10),
                'pollingInterval': so['spec'].get('pollingInterval', 30),
                'cooldownPeriod': so['spec'].get('cooldownPeriod', 60),
                'triggers': so['spec'].get('triggers', []),
                'status': so.get('status', {}),
                'conditions': so.get('status', {}).get('conditions', [])
            })
        return jsonify({'exists': False})
    except Exception as e:
        if 'not found' in str(e).lower():
            return jsonify({'exists': False})
        return jsonify({'error': str(e)}), 500

@app.route('/api/otel-metrics')
def get_otel_metrics():
    try:
        # Get raw Prometheus metrics from OTEL Collector
        resp = requests.get(f"http://otel-collector.{NAMESPACE}.svc:8889/metrics", timeout=5)
        if resp.status_code == 200:
            # Parse and extract key metrics
            lines = resp.text.split('\n')
            metrics = {}

            for line in lines:
                if line.startswith('#') or not line.strip():
                    continue

                # Look for specific metrics
                if 'pending_orders' in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        metrics['pending_orders'] = parts[-1]
                elif 'processed_orders_total' in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        metrics['processed_orders_total'] = parts[-1]
                elif 'otelcol_receiver_accepted_metric_points' in line and 'receiver="otlp"' in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        metrics['receiver_accepted_points'] = parts[-1]
                elif 'otelcol_exporter_sent_metric_points' in line and 'exporter="prometheus"' in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        metrics['exporter_sent_points'] = parts[-1]

            return jsonify({
                'metrics': metrics,
                'raw': resp.text
            })
        else:
            return jsonify({'error': f'Failed to fetch metrics: {resp.status_code}'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# =============================================================================
# HTTP TRAFFIC DEMO ENDPOINTS
# =============================================================================

import threading
import time as pytime

# HTTP load generation state
_http_load_active = False
_http_load_workers = 0
_http_successes = []
_http_failures = []
_http_total_reqs = 0
_http_lock = threading.Lock()

HTTP_TARGET_URL = f"http://http-target-svc.{NAMESPACE}.svc:8080"

def _http_worker():
    global _http_total_reqs
    while _http_load_active:
        try:
            r = requests.post(f"{HTTP_TARGET_URL}/work", timeout=15)
            now = pytime.time()
            failed = r.status_code != 200
            with _http_lock:
                (_http_failures if failed else _http_successes).append(now)
                _http_total_reqs += 1
            if failed:
                pytime.sleep(1.0)
        except Exception:
            with _http_lock:
                _http_failures.append(pytime.time())
            pytime.sleep(0.1)

def _get_http_error_stats():
    now = pytime.time()
    with _http_lock:
        _http_successes[:] = [t for t in _http_successes if now - t < 10]
        _http_failures[:] = [t for t in _http_failures if now - t < 10]
        suc = len(_http_successes)
        fail = len(_http_failures)
    total = suc + fail
    err_rate = round(fail / total * 100, 1) if total > 0 else 0.0
    return suc, fail, err_rate

@app.route('/api/http/keda-metrics')
def http_keda_metrics():
    """Exposed for KEDA metrics-api scaler"""
    with _http_lock:
        workers = _http_load_workers
    return jsonify({'value': workers})

@app.route('/api/http/metrics')
def http_get_metrics():
    try:
        # Get replica count
        deploy_resp = k8s_get(f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/http-target")
        replicas = 1
        if deploy_resp.status_code == 200:
            deploy_data = deploy_resp.json()
            replicas = deploy_data.get('status', {}).get('replicas', 1)

        suc, fail, err_rate = _get_http_error_stats()
        with _http_lock:
            load_active = _http_load_active
            load_workers = _http_load_workers
            total_reqs = _http_total_reqs

        return jsonify({
            'replicas': replicas,
            'suc': suc,
            'fail': fail,
            'err_rate': err_rate,
            'total_rps': round((suc + fail) / 10.0, 1),
            'success_rps': round(suc / 10.0, 1),
            'total_reqs': total_reqs,
            'load_active': load_active,
            'load_workers': load_workers,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/http/load', methods=['POST'])
def http_start_load():
    global _http_load_active, _http_load_workers, _http_total_reqs
    try:
        data = freq.get_json() or {}
        workers = int(data.get('workers', 10))

        with _http_lock:
            _http_load_active = False
            _http_total_reqs = 0
        pytime.sleep(0.2)

        with _http_lock:
            _http_load_active = True
            _http_load_workers = workers

        for _ in range(workers):
            threading.Thread(target=_http_worker, daemon=True).start()

        return jsonify({'status': 'ok', 'workers': workers})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/http/load', methods=['DELETE'])
def http_stop_load():
    global _http_load_active, _http_load_workers
    try:
        with _http_lock:
            _http_load_active = False
            _http_load_workers = 0
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/http/keda', methods=['GET'])
def http_get_keda_status():
    try:
        resp = k8s_get(f"/apis/keda.sh/v1alpha1/namespaces/{NAMESPACE}/scaledobjects/http-target-scaler")
        return jsonify({'enabled': resp.status_code == 200})
    except Exception:
        return jsonify({'enabled': False})

@app.route('/api/http/keda', methods=['POST'])
def http_enable_keda():
    try:
        data = freq.get_json() or {}
        enable = data.get('enable', False)

        if enable:
            scaled_object = {
                "apiVersion": "keda.sh/v1alpha1",
                "kind": "ScaledObject",
                "metadata": {"name": "http-target-scaler", "namespace": NAMESPACE},
                "spec": {
                    "scaleTargetRef": {"name": "http-target"},
                    "minReplicaCount": 1,
                    "maxReplicaCount": 20,
                    "pollingInterval": 5,
                    "cooldownPeriod": 30,
                    "triggers": [{
                        "type": "metrics-api",
                        "metadata": {
                            "targetValue": "5",
                            "url": f"http://dashboard-svc.{NAMESPACE}.svc:8501/api/http/keda-metrics",
                            "valueLocation": "value",
                        }
                    }],
                    "advanced": {
                        "restoreToOriginalReplicaCount": True,
                        "horizontalPodAutoscalerConfig": {
                            "behavior": {
                                "scaleDown": {
                                    "stabilizationWindowSeconds": 30,
                                    "policies": [{"type": "Percent", "value": 100, "periodSeconds": 15}]
                                }
                            }
                        }
                    }
                }
            }
            resp = k8s_post(f"/apis/keda.sh/v1alpha1/namespaces/{NAMESPACE}/scaledobjects", scaled_object)
            if resp.status_code in [200, 201]:
                return jsonify({'status': 'ok', 'message': 'HTTP KEDA enabled'})
            return jsonify({'status': 'error', 'message': resp.text}), 500
        else:
            resp = k8s_delete(f"/apis/keda.sh/v1alpha1/namespaces/{NAMESPACE}/scaledobjects/http-target-scaler")
            if resp.status_code in [200, 404]:
                return jsonify({'status': 'ok', 'message': 'HTTP KEDA disabled'})
            return jsonify({'status': 'error', 'message': resp.text}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# =============================================================================
# POSTGRES QUEUE DEMO ENDPOINTS
# =============================================================================

import psycopg2

PG_CONNECTION_STRING = f"host=postgres-svc.{NAMESPACE}.svc port=5432 user=demo password=demo dbname=demo sslmode=disable"

def pg_query(query, params=None):
    """Execute postgres query and return results"""
    try:
        conn = psycopg2.connect(PG_CONNECTION_STRING)
        cur = conn.cursor()
        cur.execute(query, params or ())
        if query.strip().upper().startswith('SELECT'):
            result = cur.fetchall()
        else:
            conn.commit()
            result = None
        cur.close()
        conn.close()
        return result
    except Exception:
        return None

@app.route('/api/pg/queue')
def pg_get_queue():
    try:
        # Get job counts by status
        result = pg_query("SELECT status, COUNT(*) FROM jobs GROUP BY status")
        counts = {'pending': 0, 'processing': 0, 'done': 0}
        if result:
            for status, count in result:
                counts[status] = count

        # Get replica count
        deploy_resp = k8s_get(f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/pg-worker")
        replicas = 1
        if deploy_resp.status_code == 200:
            deploy_data = deploy_resp.json()
            replicas = deploy_data.get('status', {}).get('replicas', 1)

        return jsonify({
            'pending': counts['pending'],
            'processing': counts['processing'],
            'done': counts['done'],
            'replicas': replicas,
            'capacity': replicas * 16.67  # ~16.67 jobs/sec per worker (0.06s each)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/pg/produce', methods=['POST'])
def pg_produce_jobs():
    try:
        data = freq.get_json() or {}
        count = int(data.get('count', 1000))

        # Insert jobs in bulk
        values = ','.join([f"('payload-{i}')" for i in range(count)])
        pg_query(f"INSERT INTO jobs (payload) VALUES {values}")

        return jsonify({'status': 'ok', 'message': f'Produced {count} jobs'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/pg/reset', methods=['POST'])
def pg_reset_queue():
    try:
        pg_query("TRUNCATE TABLE jobs")
        return jsonify({'status': 'ok', 'message': 'Queue reset'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/pg/keda', methods=['GET'])
def pg_get_keda_status():
    try:
        resp = k8s_get(f"/apis/keda.sh/v1alpha1/namespaces/{NAMESPACE}/scaledobjects/pg-worker-scaler")
        return jsonify({'enabled': resp.status_code == 200})
    except Exception:
        return jsonify({'enabled': False})

@app.route('/api/pg/keda', methods=['POST'])
def pg_toggle_keda():
    try:
        data = freq.get_json() or {}
        enable = data.get('enable', False)

        if enable:
            # Create TriggerAuthentication first
            trigger_auth = {
                "apiVersion": "keda.sh/v1alpha1",
                "kind": "TriggerAuthentication",
                "metadata": {"name": "pg-trigger-auth", "namespace": NAMESPACE},
                "spec": {
                    "secretTargetRef": [{
                        "parameter": "connection",
                        "name": "postgres-secret",
                        "key": "connection"
                    }]
                }
            }
            k8s_post(f"/apis/keda.sh/v1alpha1/namespaces/{NAMESPACE}/triggerauthentications", trigger_auth)

            # Create ScaledObject
            scaled_object = {
                "apiVersion": "keda.sh/v1alpha1",
                "kind": "ScaledObject",
                "metadata": {"name": "pg-worker-scaler", "namespace": NAMESPACE},
                "spec": {
                    "scaleTargetRef": {"name": "pg-worker"},
                    "minReplicaCount": 1,
                    "maxReplicaCount": 10,
                    "pollingInterval": 5,
                    "cooldownPeriod": 30,
                    "triggers": [{
                        "type": "postgresql",
                        "metadata": {
                            "query": "SELECT COUNT(*) FROM jobs WHERE status='pending'",
                            "targetQueryValue": "50",
                            "connectionFromEnv": "connection"
                        },
                        "authenticationRef": {"name": "pg-trigger-auth"}
                    }]
                }
            }
            resp = k8s_post(f"/apis/keda.sh/v1alpha1/namespaces/{NAMESPACE}/scaledobjects", scaled_object)
            if resp.status_code in [200, 201]:
                return jsonify({'status': 'ok', 'message': 'Postgres KEDA enabled'})
            return jsonify({'status': 'error', 'message': resp.text}), 500
        else:
            k8s_delete(f"/apis/keda.sh/v1alpha1/namespaces/{NAMESPACE}/scaledobjects/pg-worker-scaler")
            k8s_delete(f"/apis/keda.sh/v1alpha1/namespaces/{NAMESPACE}/triggerauthentications/pg-trigger-auth")
            return jsonify({'status': 'ok', 'message': 'Postgres KEDA disabled'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# =============================================================================
# KAFKA LAG DEMO ENDPOINTS
# =============================================================================

@app.route('/api/kafka/lag')
def kafka_get_lag():
    try:
        # This requires kafka-python library to query consumer lag
        # For now, return placeholder data
        # In production, you'd use: from kafka import KafkaConsumer, TopicPartition

        # Get replica count
        deploy_resp = k8s_get(f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/kafka-consumer")
        replicas = 1
        if deploy_resp.status_code == 200:
            deploy_data = deploy_resp.json()
            replicas = deploy_data.get('status', {}).get('replicas', 1)

        return jsonify({
            'total_lag': 0,  # Would query from Kafka
            'replicas': replicas,
            'partitions': []  # Would include per-partition lag
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/kafka/produce', methods=['POST'])
def kafka_produce_messages():
    try:
        data = freq.get_json() or {}
        count = int(data.get('count', 1000))

        # Create Kubernetes Job to produce messages
        job = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": "bulk-producer", "namespace": NAMESPACE},
            "spec": {
                "ttlSecondsAfterFinished": 60,
                "template": {
                    "spec": {
                        "restartPolicy": "Never",
                        "containers": [{
                            "name": "producer",
                            "image": "quay.io/strimzi/kafka:0.51.0-kafka-4.1.0",
                            "command": ["/bin/sh", "-c",
                                f"seq 1 {count} | awk '{{print \"message-\" $1}}' | "
                                "/opt/kafka/bin/kafka-console-producer.sh "
                                "--bootstrap-server kafka-lag-cluster-kafka-bootstrap:9092 "
                                f"--topic demo-topic && echo Done producing {count} messages"
                            ]
                        }]
                    }
                }
            }
        }

        # Delete existing job if present
        k8s_delete(f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs/bulk-producer")
        pytime.sleep(0.5)

        # Create new job
        resp = k8s_post(f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs", job)
        if resp.status_code in [200, 201]:
            return jsonify({'status': 'ok', 'message': f'Producing {count} messages'})
        return jsonify({'status': 'error', 'message': resp.text}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/kafka/keda', methods=['GET'])
def kafka_get_keda_status():
    try:
        resp = k8s_get(f"/apis/keda.sh/v1alpha1/namespaces/{NAMESPACE}/scaledobjects/kafka-consumer-scaler")
        return jsonify({'enabled': resp.status_code == 200})
    except Exception:
        return jsonify({'enabled': False})

@app.route('/api/kafka/keda', methods=['POST'])
def kafka_toggle_keda():
    try:
        data = freq.get_json() or {}
        enable = data.get('enable', False)

        if enable:
            scaled_object = {
                "apiVersion": "keda.sh/v1alpha1",
                "kind": "ScaledObject",
                "metadata": {"name": "kafka-consumer-scaler", "namespace": NAMESPACE},
                "spec": {
                    "scaleTargetRef": {"name": "kafka-consumer"},
                    "minReplicaCount": 1,
                    "maxReplicaCount": 10,
                    "pollingInterval": 5,
                    "cooldownPeriod": 30,
                    "triggers": [{
                        "type": "kafka",
                        "metadata": {
                            "bootstrapServers": f"kafka-lag-cluster-kafka-bootstrap.{NAMESPACE}.svc:9092",
                            "consumerGroup": "slow-consumer-group",
                            "topic": "demo-topic",
                            "lagThreshold": "500"
                        }
                    }]
                }
            }
            resp = k8s_post(f"/apis/keda.sh/v1alpha1/namespaces/{NAMESPACE}/scaledobjects", scaled_object)
            if resp.status_code in [200, 201]:
                return jsonify({'status': 'ok', 'message': 'Kafka KEDA enabled'})
            return jsonify({'status': 'error', 'message': resp.text}), 500
        else:
            resp = k8s_delete(f"/apis/keda.sh/v1alpha1/namespaces/{NAMESPACE}/scaledobjects/kafka-consumer-scaler")
            if resp.status_code in [200, 404]:
                return jsonify({'status': 'ok', 'message': 'Kafka KEDA disabled'})
            return jsonify({'status': 'error', 'message': resp.text}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8501, debug=False)
