# Stage 1D-A — Offline Observability Foundation

Status: Offline configuration prepared and locally validated.
Live deployment remains separately approval-gated.

## Architecture

- Pinned kube-prometheus-stack 92.11.0.
- Prometheus: one replica, six-hour retention, ephemeral storage.
- Grafana: private ClusterIP and localhost port-forward only.
- Alertmanager: one replica, ephemeral storage, local receiver.
- kube-state-metrics and node-exporter enabled.
- FastAPI DEV ServiceMonitor derived from rendered backend Service.
- Grafana FastAPI dashboard discovered through labelled ConfigMap.
- Existing platform AppProject and root manual-sync policy preserved.
- No ALB, EBS PVC or additional worker nodes.

## Outstanding live acceptance

- Confirm actual two-node allocatable CPU/memory headroom.
- Verify FastAPI metric family names against live `/metrics`.
- Verify ServiceMonitor scraping and Grafana dashboard queries.
- Provision Grafana admin credentials through approved secret management.
- Review Prometheus Operator CRDs and cluster-scoped RBAC.
- Verify Prometheus targets, Alertmanager rules and resource consumption.
- Export monitoring evidence before approved shutdown.

## Lifecycle and cost

CloudWatch cost controls remain Stage 1D-C.
Lifecycle Batch 2 must verify monitoring recovery and RDS schema readiness.
External ALB acceptance remains deferred under Stage 1C.
