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
        if node.get("verified") is False and node.get("operation") in ("shutdown", "restore"):
            results.append({"type": "lifecycle_unverified", "source": location})
    return results[:100]
