#!/usr/bin/env python3
"""Offline syntax and invariant checks for the Kubernetes platform source."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as error:
    raise SystemExit("BLOCKED: install PyYAML to validate platform YAML.") from error


ROOT = Path(__file__).resolve().parents[1]


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate mapping keys instead of silently taking the last one."""


def construct_unique_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_unique_mapping
)


def load_yaml_files() -> tuple[int, dict[Path, list[dict[str, Any]]]]:
    yaml_paths = sorted(
        path
        for directory in (ROOT / "gitops", ROOT / "platform" / "bootstrap")
        if directory.exists()
        for path in directory.rglob("*")
        if path.suffix in {".yaml", ".yml"}
    )
    loaded: dict[Path, list[dict[str, Any]]] = {}

    for path in yaml_paths:
        try:
            with path.open(encoding="utf-8") as stream:
                documents = [
                    document
                    for document in yaml.load_all(stream, Loader=UniqueKeyLoader)
                    if document is not None
                ]
        except (OSError, yaml.YAMLError) as error:
            raise SystemExit(f"FAIL: {path.relative_to(ROOT)}: {error}") from error

        for document in documents:
            if not isinstance(document, dict):
                raise SystemExit(
                    f"FAIL: {path.relative_to(ROOT)} contains a non-mapping YAML document."
                )
            if document.get("kind") == "Kustomization":
                continue
            if "kind" in document and not all(
                key in document for key in ("apiVersion", "metadata")
            ):
                raise SystemExit(
                    f"FAIL: {path.relative_to(ROOT)} has an incomplete Kubernetes object."
                )
        loaded[path.relative_to(ROOT)] = documents

    if not yaml_paths:
        raise SystemExit("FAIL: no platform YAML files were found.")
    return len(yaml_paths), loaded


def validate_platform_invariants(
    documents: dict[Path, list[dict[str, Any]]]
) -> None:
    storage_path = Path("gitops/platform/storageclass-gp3.yaml")
    storage_docs = documents.get(storage_path, [])
    storage = next(
        (document for document in storage_docs if document.get("kind") == "StorageClass"),
        None,
    )
    if not storage or storage.get("provisioner") != "ebs.csi.aws.com":
        raise SystemExit("FAIL: gp3 must use the EBS CSI provisioner.")
    if storage.get("parameters", {}).get("type") != "gp3":
        raise SystemExit("FAIL: gp3 StorageClass must specify EBS volume type gp3.")
    if storage.get("reclaimPolicy") != "Delete":
        raise SystemExit("FAIL: disposable lab gp3 volumes must be deleted with their PVCs.")
    if storage.get("volumeBindingMode") != "WaitForFirstConsumer":
        raise SystemExit("FAIL: gp3 must defer binding until pod scheduling.")
    bootstrap_storage_docs = documents.get(
        Path("platform/bootstrap/storageclass-gp3.yaml"), []
    )
    bootstrap_storage = next(
        (
            document
            for document in bootstrap_storage_docs
            if document.get("kind") == "StorageClass"
        ),
        None,
    )
    storage_fields = (
        "metadata",
        "provisioner",
        "parameters",
        "reclaimPolicy",
        "allowVolumeExpansion",
        "volumeBindingMode",
    )
    if not bootstrap_storage or any(
        bootstrap_storage.get(field) != storage.get(field) for field in storage_fields
    ):
        raise SystemExit("FAIL: bootstrap and GitOps gp3 StorageClass definitions must match.")

    namespace_docs = documents.get(Path("gitops/platform/namespaces.yaml"), [])
    namespaces = {
        document.get("metadata", {}).get("name"): document
        for document in namespace_docs
        if document.get("kind") == "Namespace"
    }
    if namespaces.get("dev", {}).get("metadata", {}).get("labels", {}).get(
        "pod-security.kubernetes.io/enforce"
    ) != "restricted":
        raise SystemExit("FAIL: dev namespace must enforce the restricted Pod Security standard.")

    policy_path = ROOT / "infra" / "terraform" / "modules" / "iam" / "aws-lbc-iam-policy.json"
    try:
        policy_text = policy_path.read_text(encoding="utf-8")
        policy = json.loads(policy_text)
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"FAIL: invalid AWS LBC IAM policy JSON: {error}") from error
    if len("".join(policy_text.split())) > 6144:
        raise SystemExit("FAIL: AWS LBC managed policy exceeds the 6,144-character IAM limit.")
    statements = policy.get("Statement", [])
    if not any(
        "aws:RequestTag/elbv2.k8s.aws/cluster"
        in statement.get("Condition", {}).get("Null", {})
        for statement in statements
    ):
        raise SystemExit("FAIL: AWS LBC policy lost its controller cluster-tag guard.")
    policy_actions = {
        action
        for statement in statements
        for action in statement.get("Action", [])
    }
    if any(action.startswith(("shield:", "waf-regional:", "wafv2:")) for action in policy_actions):
        raise SystemExit("FAIL: optional WAF and Shield permissions must remain excluded from the LBC role.")

    lbc_values = documents.get(Path("gitops/platform/values/aws-lbc-values.yaml"), [])
    lbc = lbc_values[0] if lbc_values else {}
    if any(lbc.get(feature) is not False for feature in ("enableWaf", "enableWafv2", "enableShield")):
        raise SystemExit("FAIL: optional WAF and Shield integrations must remain disabled.")

    eso_values = documents.get(Path("gitops/platform/values/external-secrets-values.yaml"), [])
    eso = eso_values[0] if eso_values else {}
    if eso.get("scopedNamespace") != "dev" or eso.get("scopedRBAC") is not True:
        raise SystemExit("FAIL: External Secrets must remain namespace-scoped to dev.")
    if any(
        eso.get(key) is not False
        for key in (
            "processClusterExternalSecret",
            "processClusterStore",
            "processClusterGenerator",
            "processClusterPushSecret",
            "processPushSecret",
        )
    ):
        raise SystemExit("FAIL: unused cluster-scoped and push-secret controllers must remain disabled.")

    root_app = documents.get(Path("gitops/argocd/platform-root.yaml"), [])
    root_application = next(
        (document for document in root_app if document.get("kind") == "Application"),
        None,
    )
    if not root_application or root_application.get("metadata", {}).get("finalizers"):
        raise SystemExit("FAIL: root Application must not cascade-delete platform resources.")
    if root_application.get("spec", {}).get("syncPolicy", {}).get("automated", {}).get("prune") is not False:
        raise SystemExit("FAIL: root Application automated pruning must remain disabled.")

    project_docs = documents.get(Path("gitops/platform/platform-project.yaml"), [])
    project = next(
        (document for document in project_docs if document.get("kind") == "AppProject"),
        None,
    )
    if not project or len(project.get("spec", {}).get("destinations", [])) != 4:
        raise SystemExit("FAIL: platform AppProject must restrict destinations to the four platform namespaces.")

    for relative_path in (
        Path("gitops/platform/apps/aws-load-balancer-controller.yaml"),
        Path("gitops/platform/apps/external-secrets.yaml"),
        Path("gitops/platform/apps/dev-secrets.yaml"),
    ):
        app = next(
            (document for document in documents.get(relative_path, []) if document.get("kind") == "Application"),
            None,
        )
        if not app or app.get("spec", {}).get("project") != "platform":
            raise SystemExit(f"FAIL: {relative_path} must use the restricted platform AppProject.")

    dev_secrets_app = documents.get(Path("gitops/platform/apps/dev-secrets.yaml"), [])
    application = next(
        (document for document in dev_secrets_app if document.get("kind") == "Application"),
        None,
    )
    if application and "syncPolicy" in application.get("spec", {}):
        raise SystemExit("FAIL: dev-secrets must remain manually synced after ESO CRDs are ready.")


def main() -> None:
    file_count, documents = load_yaml_files()
    validate_platform_invariants(documents)
    print(
        f"PASS: parsed {file_count} platform YAML files; storage, namespace, IAM-policy and sync-order invariants passed."
    )
    print("NOTE: this is offline syntax validation, not Kubernetes API or Helm chart validation.")


if __name__ == "__main__":
    main()