"""Read-only, bounded parsers for saved platform evidence (no cloud clients)."""
import json

MAX_NODES = 2000

def _walk(obj):
    stack = [("$", obj)]
    count = 0
    while stack:
        path, node = stack.pop()
        count += 1
        if count > MAX_NODES:
            raise ValueError("structured evidence exceeds node limit")
        yield path, node
        if isinstance(node, dict):
            for key, value in list(node.items())[:300]:
                if isinstance(value, (dict, list)):
                    stack.append((path + "." + str(key)[:70], value))
        elif isinstance(node, list):
            for index, value in enumerate(node[:300]):
                if isinstance(value, (dict, list)):
                    stack.append((f"{path}[{index}]", value))

def collect_saved_json(text):
    """Return observations only. Never assert causality or execute commands."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    results = []
    for location, node in _walk(data):
        if not isinstance(node, dict):
            continue
        terminated = node.get("terminated")
        if isinstance(terminated, dict) and terminated.get("reason") == "OOMKilled":
            results.append({"type": "kubernetes_termination", "source": location,
                            "reason": "OOMKilled", "exit_code": terminated.get("exitCode")})
        if node.get("reason") == "FailedScheduling":
            results.append({"type": "kubernetes_scheduling", "source": location,
                            "reason": "FailedScheduling"})
        if node.get("reason") in ("FailedMount", "FailedCreatePodSandBox"):
            results.append({"type": "kubernetes_event", "source": location,
                            "reason": node["reason"]})
        if node.get("health") == "down" and ("labels" in node or "lastError" in node):
            results.append({"type": "prometheus_target", "source": location, "health": "down"})
        if node.get("health") == "Degraded" and "sync" in node:
            results.append({"type": "argocd_application", "source": location, "health": "Degraded"})
        if node.get("health") == "Healthy" and isinstance(node.get("sync"), dict):
            if node["sync"].get("status") == "OutOfSync":
                results.append({"type": "argocd_drift", "source": location, "sync": "OutOfSync"})
        changes = node.get("change")
        if isinstance(changes, dict) and isinstance(changes.get("actions"), list):
            actions = changes["actions"]
            if "delete" in actions and "create" in actions:
                results.append({"type": "terraform_replacement", "source": location,
                                "address": str(node.get("address", ""))[:160]})
        # Argo CD status from saved `argocd app get -o json` output.
        if isinstance(node.get("sync"), dict) and isinstance(node.get("health"), dict):
            sync_status = node["sync"].get("status")
            health_status = node["health"].get("status")
            if sync_status == "OutOfSync" or health_status == "Degraded":
                results.append({"type": "argocd_state", "source": location,
                                "sync": sync_status, "health": health_status})
        # Kubernetes Pod status snapshots.
        if node.get("phase") == "Pending" and "containerStatuses" in node:
            results.append({"type": "kubernetes_pod_pending", "source": location})
        # Prometheus Alertmanager alert snapshots.
        if node.get("state") == "firing" and isinstance(node.get("labels"), dict):
            results.append({"type": "prometheus_alert_firing", "source": location,
                            "alertname": str(node["labels"].get("alertname", ""))[:100]})
        # A Terraform plan is evidence, not a request to execute the actions.
        if isinstance(changes, dict) and isinstance(changes.get("actions"), list):
            if changes["actions"] == ["delete"]:
                results.append({"type": "terraform_deletion", "source": location,
                                "address": str(node.get("address", ""))[:160]})
        # Lifecycle checkpoint metadata can indicate unresolved stages.
        if node.get("status") in ("running", "failed") and "result" in node:
            results.append({"type": "lifecycle_stage_unresolved", "source": location,
                            "status": node["status"]})
        # PostgreSQL and application health reports.
        if node.get("schema_and_permissions_verified") is False:
            results.append({"type": "postgresql_schema_unverified", "source": location})
        # GitHub Actions exported JSON job status.
        if node.get("conclusion") == "failure" and "steps" in node:
            results.append({"type": "github_actions_failed_job", "source": location})
        if node.get("verified") is False and node.get("operation") in ("shutdown", "restore"):
            results.append({"type": "lifecycle_unverified", "source": location})
    return results[:100]
