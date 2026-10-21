#!/usr/bin/env python3
"""Checkpointed Stage 3 platform recovery and certification checks.

This module keeps the existing command entry point while reconciling live state
before every write. It never restores Terraform or PostgreSQL data itself.
"""

from __future__ import annotations
import yaml

import getpass
import json
import re
import secrets
import shutil
import sys
import warnings
from dataclasses import dataclass
from typing import Callable

from bootstrap import RecoveryBootstrap
from foundation import (
    CheckpointStore,
    LabConfig,
    LifecycleError,
    ROOT,
    atomic_json_write,
    utc_now,
)
from lifecycle import (
    account_guard,
    call,
    client_pod,
    database_connection,
    environment,
    output,
    terraform_values,
)


ARGOCD_CHART_VERSION = "10.10.3"
ALEMBIC_HEAD = "fe56fa70289e"
STAGE3_REVISION = "540ed080c0dd52f0e6855c754f687f999887dc9f"
GRAFANA_SECRET = "observability-grafana-admin"

OBSERVABILITY_CRDS = (
    "alertmanagerconfigs.monitoring.coreos.com",
    "alertmanagers.monitoring.coreos.com",
    "podmonitors.monitoring.coreos.com",
    "probes.monitoring.coreos.com",
    "prometheusagents.monitoring.coreos.com",
    "prometheuses.monitoring.coreos.com",
    "prometheusrules.monitoring.coreos.com",
    "scrapeconfigs.monitoring.coreos.com",
    "servicemonitors.monitoring.coreos.com",
    "thanosrulers.monitoring.coreos.com",
)

PLATFORM_STAGES = (
    "argocd",
    "gitops_dependencies",
    "controllers",
    "observability_crds",
    "grafana_secret",
    "observability",
    "stage3_application",
)

OBSERVABILITY_WORKLOADS = (
    ("statefulset", "prometheus-observability-prometheus",
     "app.kubernetes.io/managed-by=prometheus-operator,release=observability"),
    ("statefulset", "alertmanager-observability-alertmanager",
     "app.kubernetes.io/managed-by=prometheus-operator,release=observability"),
    ("deployment", "observability-grafana",
     "app.kubernetes.io/instance=observability"),
    ("deployment", "observability-kube-state-metrics",
     "app.kubernetes.io/instance=observability"),
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LifecycleError(message)


def _resource_ready(resource: dict) -> bool:
    spec = resource.get("spec", {})
    status = resource.get("status", {})
    desired = spec.get("replicas", 1)
    return (
        status.get("observedGeneration")
        == resource.get("metadata", {}).get("generation")
        and status.get("readyReplicas", 0) == desired
        and status.get("availableReplicas", desired) == desired
    )


def _manual_sync(application: dict) -> bool:
    if not isinstance(application, dict):
        return False
    spec = application.get("spec")
    if not isinstance(spec, dict):
        return False
    policy = spec.get("syncPolicy", {})
    if not isinstance(policy, dict):
        return False
    if "automated" not in policy:
        return True
    automated = policy["automated"]
    return (
        isinstance(automated, dict)
        and automated.get("enabled") is False
    )


def _application_ready(application: dict) -> bool:
    status = application.get("status", {})
    return (
        status.get("sync", {}).get("status") == "Synced"
        and status.get("health", {}).get("status") == "Healthy"
    )


def _expected_stage3_images() -> set[str]:
    values = (ROOT / "app/helm/values-dev.yaml").read_text(encoding="utf-8")
    images = set(re.findall(r'image:\s*"([^"\n]+)"', values))
    _require(len(images) == 2, "Expected exactly two Stage 3 application images")
    _require(
        all(re.search(r"@sha256:[0-9a-f]{64}$", image) for image in images),
        "Stage 3 application images must use immutable digests",
    )
    return images


def validate_source_contract() -> None:
    """Validate commit-owned recovery invariants without external access."""
    values = (ROOT / "platform/bootstrap/argocd-values.yaml").read_text(
        encoding="utf-8"
    )
    controller_values = values.split("repoServer:", 1)[0]
    _require(
        "memory: 512Mi" in controller_values,
        "Argo CD controller request is not 512Mi",
    )
    _require(
        "memory: 1Gi" in controller_values,
        "Argo CD controller limit is not 1Gi",
    )

    root = (ROOT / "gitops/argocd/platform-root.yaml").read_text(
        encoding="utf-8"
    )
    _require("enabled: false" in root, "Platform root manual sync is not preserved")
    _require("selfHeal: false" in root, "Platform root self-heal must remain disabled")

    stage3 = (ROOT / "gitops/platform/stage3-dev.yaml").read_text(
        encoding="utf-8"
    )
    _require(
        f"targetRevision: {STAGE3_REVISION}" in stage3,
        "Stage 3 Application revision changed",
    )

    listed = {
        line.removeprefix("-").strip()
        for line in (
            ROOT / "gitops/platform/crds/observability/kustomization.yaml"
        ).read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith("-")
    }
    expected = {name + ".yaml" for name in OBSERVABILITY_CRDS}
    _require(listed == expected, "Observability CRD set is not the approved ten")
    _expected_stage3_images()


Executor = Callable[..., bytes]


@dataclass
class PlatformRecovery:
    config: LabConfig
    checkpoint: CheckpointStore
    executor: Executor = call
    bootstrap: RecoveryBootstrap | None = None

    def run(self, args, *, data: bytes | None = None, timeout: int = 1200) -> bytes:
        return self.executor(
            args,
            data=data,
            timeout=timeout,
            env=environment(self.config),
        )

    def text(self, args, *, timeout: int = 1200) -> str:
        return self.run(args, timeout=timeout).decode().strip()

    def json(self, args, *, timeout: int = 1200) -> dict:
        raw = self.text(args, timeout=timeout)
        value = json.loads(raw)
        _require(isinstance(value, dict), "Expected JSON object")
        return value

    def optional_json(self, args, *, timeout: int = 1200) -> dict:
        raw = self.text(args, timeout=timeout)
        if not raw:
            return {}
        value = json.loads(raw)
        _require(isinstance(value, dict), "Expected JSON object")
        return value

    def verify_identity(self, *, update_kubeconfig: bool = True) -> dict:
        account_guard(self.config)
        values = terraform_values(self.config)
        _require(
            values.get("cluster_name") == self.config.cluster_name,
            "Terraform cluster mismatch",
        )
        expected_arn = (
            f"arn:aws:eks:{self.config.aws_region}:"
            f"{self.config.expected_account_id}:cluster/{self.config.cluster_name}"
        )
        _require(values.get("cluster_arn") == expected_arn, "Terraform EKS ARN mismatch")

        cluster = self.json([
            "aws", "eks", "describe-cluster", "--name", self.config.cluster_name,
            "--output", "json",
        ])["cluster"]
        _require(cluster.get("arn") == expected_arn, "Live EKS ARN mismatch")
        _require(cluster.get("status") == "ACTIVE", "EKS cluster is not ACTIVE")

        if update_kubeconfig:
            self.run([
                "aws", "eks", "update-kubeconfig", "--name", self.config.cluster_name,
                "--region", self.config.aws_region, "--alias", "eks-reliability-lab",
            ])
        context = self.text(["kubectl", "config", "current-context"])
        _require(bool(context), "Kubeconfig current context is unavailable")
        server = self.text([
            "kubectl", "config", "view", "--minify",
            "-o", "jsonpath={.clusters[0].cluster.server}",
        ])
        _require(server == cluster.get("endpoint"), "Kubeconfig endpoint mismatch")
        ca = self.text([
            "kubectl", "config", "view", "--raw", "--minify",
            "-o", "jsonpath={.clusters[0].cluster.certificate-authority-data}",
        ])

        _require(
            bool(ca)
            and ca == cluster.get("certificateAuthority", {}).get("data"),
            "Kubeconfig certificate authority mismatch",
        )
        return values

    def _get_application(self, name: str) -> dict:
        return self.optional_json([
            "kubectl", "-n", "argocd", "get", "application", name,
            "--ignore-not-found", "-o", "json",
        ])

    def _verified_argocd_application(self, name: str) -> dict:
        """Verify CLI and Kubernetes identify the same approved Application."""
        self.verify_identity(update_kubeconfig=False)
        _require(
            shutil.which("argocd", path=environment(self.config).get("PATH"))
            is not None,
            "Argo CD CLI is unavailable on PATH",
        )

        live = self._get_application(name)
        metadata = live.get("metadata", {})
        uid = metadata.get("uid")

        _require(
            bool(uid)
            and metadata.get("name") == name
            and metadata.get("namespace") == "argocd",
            "Argo CD Kubernetes Application identity unavailable",
        )

        cli = self.json([
            "argocd", "app", "get", name, "-o", "json",
        ])

        cli_meta = cli.get("metadata", {})

        _require(
            cli_meta.get("uid") == uid
            and cli_meta.get("name") == name
            and cli_meta.get("namespace") == "argocd",
            "Argo CD CLI Application identity mismatch",
        )

        _require(
            cli.get("spec") == live.get("spec"),
            "Argo CD CLI Application specification mismatch",
        )

        destination = live.get("spec", {}).get("destination", {})

        _require(
            destination.get("server") == "https://kubernetes.default.svc"
            and not destination.get("name"),
            "Argo CD Application destination not approved",
        )

        return live

    def _sync_application(
        self, name: str, *options: str, timeout: int = 1200
    ) -> None:
        application = self._verified_argocd_application(name)

        _require(
            _manual_sync(application),
            "Argo CD manual synchronization required",
        )

        _require(
            "--prune" not in options and "--force" not in options,
            "Unsafe Argo CD synchronization option",
        )

        self.run(
            ["argocd", "app", "sync", name, *options],
            timeout=timeout,
        )

    def _argocd_release(self) -> dict | None:
        releases = json.loads(self.text([
            "helm", "list", "-n", "argocd", "-o", "json",
        ]))
        matches = [item for item in releases if item.get("name") == "argocd"]
        _require(len(matches) <= 1, "Multiple Argo CD Helm releases found")
        return matches[0] if matches else None

    def argocd_ready(self) -> bool:
        release = self._argocd_release()
        if not release:
            return False
        if release.get("status") != "deployed":
            return False
        if release.get("chart") != f"argo-cd-{ARGOCD_CHART_VERSION}":
            raise LifecycleError("Unexpected Argo CD chart version")

        controller = self.optional_json([
            "kubectl", "-n", "argocd", "get", "statefulset",
            "argocd-application-controller", "--ignore-not-found", "-o", "json",
        ])
        if not controller:
            return False
        container = controller["spec"]["template"]["spec"]["containers"][0]
        resources = container.get("resources", {})
        return (
            _resource_ready(controller)
            and resources.get("requests", {}).get("memory") == "512Mi"
            and resources.get("limits", {}).get("memory") == "1Gi"
        )

    def restore_argocd(self) -> None:
        release = self._argocd_release()
        if release and release.get("chart") != f"argo-cd-{ARGOCD_CHART_VERSION}":
            raise LifecycleError("Refusing unreviewed Argo CD chart change")

        self.run([
            "helm", "repo", "add", "argo", "https://argoproj.github.io/argo-helm",
            "--force-update",
        ])
        self.run(["helm", "repo", "update"])
        self.run([
            "helm", "upgrade", "--install", "argocd", "argo/argo-cd",
            "--namespace", "argocd", "--create-namespace",
            "--version", ARGOCD_CHART_VERSION,
            "--values", "platform/bootstrap/argocd-values.yaml",
            "--wait", "--timeout", "15m",
        ], timeout=1100)

    def _namespace_contract(self) -> dict:
        path = ROOT / "gitops/platform/namespaces.yaml"
        docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        result = {
            item["metadata"]["name"]: item
            for item in docs
            if isinstance(item, dict) and item.get("kind") == "Namespace"
        }
        _require(
            set(result) == {"argocd", "platform-system", "dev"},
            "Unexpected approved namespace inventory",
        )
        expected = {
            "argocd": "baseline",
            "platform-system": "baseline",
            "dev": "restricted",
        }
        for name, enforce in expected.items():
            labels = result[name]["metadata"].get("labels", {})
            _require(
                labels.get("pod-security.kubernetes.io/enforce") == enforce
                and labels.get("pod-security.kubernetes.io/audit") == "restricted"
                and labels.get("pod-security.kubernetes.io/warn") == "restricted",
                "Approved namespace PSA configuration changed",
            )
        return result

    def namespaces_ready(self) -> bool:
        for name, desired in self._namespace_contract().items():
            actual = self.optional_json([
                "kubectl", "get", "namespace", name,
                "--ignore-not-found", "-o", "json",
            ])
            if not actual:
                return False
            metadata = actual.get("metadata", {})
            if metadata.get("name") != name:
                return False
            if metadata.get("deletionTimestamp"):
                return False
            labels = metadata.get("labels", {})
            if any(
                labels.get(key) != value
                for key, value in desired["metadata"]["labels"].items()
            ):
                return False
        return True

    def gitops_dependencies_ready(self) -> bool:
            bootstrap = self.optional_json([
                "kubectl", "-n", "argocd", "get", "appproject",
                "platform-bootstrap", "--ignore-not-found", "-o", "json",
            ])
            platform = self.optional_json([
                "kubectl", "-n", "argocd", "get", "appproject", "platform",
                "--ignore-not-found", "-o", "json",
            ])
            root = self._get_application("platform-root")
            apps = [
                self._get_application(name)
                for name in ("observability-crds", "observability", "stage3-dev")
            ]
            cluster_kinds = {
                (item.get("group"), item.get("kind"))
                for item in platform.get("spec", {}).get("clusterResourceWhitelist", [])
            }
            return (
                self.namespaces_ready()
                and
                bootstrap.get("metadata", {}).get("name") == "platform-bootstrap"
                and ("apiextensions.k8s.io", "CustomResourceDefinition") in cluster_kinds
                and _manual_sync(root)
                and all(_manual_sync(app) for app in apps)
            )

    def restore_gitops_dependencies(self) -> None:
            # AppProjects must exist before Applications that reference them.
            self.run(["kubectl", "apply", "-f", "gitops/argocd/platform-bootstrap-project.yaml"])
            self.run(["kubectl", "apply", "-f", "gitops/platform/platform-project.yaml"])
            self.run(["kubectl", "apply", "-f", "gitops/argocd/platform-root.yaml"])
            # Reconcile approved child Application definitions only.
            # Never synchronize arbitrary root drift.
            approved = (
                "aws-load-balancer-controller",
                "external-secrets",
                "dev-secrets",
                "observability-crds",
                "observability",
                "stage3-dev",
            )

            options = tuple(
                option
                for name in approved
                for option in (
                    "--resource",
                    f"argoproj.io:Application:{name}",
                )
            )

            if not self.namespaces_ready():
                self._sync_application(
                    "platform-root",
                    *tuple(
                        value
                        for name in self._namespace_contract()
                        for value in ("--resource", f":Namespace:{name}")
                    ),
                    timeout=1200,
                )
            _require(
                self.namespaces_ready(),
                "Required namespaces or PSA labels not reconciled",
            )

            self._sync_application(
                "platform-root", *options, timeout=1200,
            )

            for name in approved:
                child = self._get_application(name)

                _require(
                    child.get("metadata", {}).get("name") == name
                    and _manual_sync(child),
                    f"Bootstrap Application not reconciled: {name}",
                )

    def _load_balancer_controller_state(self) -> str:
        application = self._get_application("aws-load-balancer-controller")
        _require(
            isinstance(application, dict)
            and application.get("metadata", {}).get("name")
            == "aws-load-balancer-controller",
            "Load Balancer Controller Application identity unavailable",
        )
        _require(
            _manual_sync(application),
            "Load Balancer Controller must remain manually synchronized",
        )

        deployment = self.optional_json([
            "kubectl", "-n", "kube-system", "get", "deployment",
            "aws-load-balancer-controller",
            "--ignore-not-found", "-o", "json",
        ])

        health = application.get("status", {}).get("health", {}).get("status")

        if deployment:
            if health == "Degraded":
                return "degraded"
            _require(
                health == "Healthy",
                "Existing Load Balancer Controller health is unknown",
            )
            _require(
                _resource_ready(deployment),
                "Existing Load Balancer Controller is not Ready",
            )
            return "healthy"

        _require(
            health in ("Missing", "Progressing"),
            "Missing controller not established from GitOps health",
        )
        return "absent"

    def controllers_ready(self) -> bool:
            if self._load_balancer_controller_state() != "healthy":
                return False
            for name in ("external-secrets", "dev-secrets"):
                app = self._get_application(name)
                if not _manual_sync(app) or not _application_ready(app):
                    return False
            for namespace, name in (
                ("kube-system", "aws-load-balancer-controller"),
                ("platform-system", "external-secrets"),
            ):
                deployment = self.optional_json([
                    "kubectl", "-n", namespace, "get", "deployment", name,
                    "--ignore-not-found", "-o", "json",
                ])
                if not _resource_ready(deployment):
                    return False
            for name in ("rds-credentials", "fastapi-auth"):
                external = self.optional_json([
                    "kubectl", "-n", "dev", "get", "externalsecret", name,
                    "--ignore-not-found", "-o", "json",
                ])
                ready = any(
                    item.get("type") == "Ready" and item.get("status") == "True"
                    for item in external.get("status", {}).get("conditions", [])
                )
                if not ready:
                    return False
            return True

    def restore_controllers(self) -> None:
            state = self._load_balancer_controller_state()
            if state == "degraded":
                raise LifecycleError(
                    "Existing Load Balancer Controller Degraded; manual review required"
                )
            if state == "absent":
                self._sync_application(
                    "aws-load-balancer-controller", timeout=960,
                )
                self.run([
                    "argocd", "app", "wait",
                    "aws-load-balancer-controller",
                    "--sync", "--health", "--timeout", "900",
                ], timeout=960)
                _require(
                    self._load_balancer_controller_state() == "healthy",
                    "Fresh Load Balancer Controller did not become Ready",
                )
            # Healthy OutOfSync controller is intentionally left unchanged.

            for name, timeout in (
                ("external-secrets", "900"),
                ("dev-secrets", "600"),
            ):
                self._sync_application(name, timeout=int(timeout) + 60)
                self.run([
                    "argocd", "app", "wait", name, "--sync", "--health",
                    "--timeout", timeout,
                ], timeout=int(timeout) + 60)

    def observability_crds_ready(self) -> bool:
        application = self._get_application("observability-crds")
        if not _manual_sync(application) or not _application_ready(application):
            return False
        for name in OBSERVABILITY_CRDS:
            crd = self.optional_json([
                "kubectl", "get", "crd", name, "--ignore-not-found", "-o", "json"
            ])
            established = any(
                condition.get("type") == "Established"
                and condition.get("status") == "True"
                for condition in crd.get("status", {}).get("conditions", [])
            )
            if not established:
                return False
        return True

    def restore_observability_crds(self) -> None:
        self._sync_application("observability-crds", timeout=1200)
        self.run([
            "argocd", "app", "wait", "observability-crds", "--sync", "--health",
            "--timeout", "900",
        ], timeout=960)
        for name in OBSERVABILITY_CRDS:
            self.run([
                "kubectl", "wait", "--for=condition=Established", "crd/" + name,
                "--timeout=180s",
            ], timeout=220)

    def _grafana_secret(self) -> dict | None:
        raw = self.text([
            "kubectl", "-n", "platform-system", "get", "secret", GRAFANA_SECRET,
            "--ignore-not-found", "-o", "json",
        ])
        if not raw:
            return None
        value = json.loads(raw)
        _require(isinstance(value, dict), "Invalid Grafana Secret response")
        return value

    def grafana_secret_ready(self) -> bool:
        secret = self._grafana_secret()
        return bool(
            secret
            and secret.get("type") == "Opaque"
            and secret.get("data", {}).get("admin-user")
            and secret.get("data", {}).get("admin-password")
        )

    def _grafana_credentials(self) -> tuple[str, str]:
        try:
            # Separate descriptors avoid direction changes on a non-seekable TTY.
            with (
                open("/dev/tty", "r", encoding="utf-8", buffering=1) as terminal_in,
                open("/dev/tty", "w", encoding="utf-8", buffering=1) as terminal_out,
            ):
                terminal_out.write("Grafana admin user [admin]: ")
                terminal_out.flush()
                raw_username = terminal_in.readline()
                if not raw_username:
                    raise EOFError("Interactive terminal closed")
                username = raw_username.strip() or "admin"
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    password = getpass.getpass(
                        "Grafana admin password: ", stream=terminal_out
                    )
                    confirm = getpass.getpass(
                        "Confirm Grafana admin password: ", stream=terminal_out
                    )
        except (OSError, EOFError, getpass.GetPassWarning) as exc:
            raise LifecycleError("Interactive terminal required for Grafana Secret") from exc
        _require(password == confirm, "Grafana passwords do not match")
        _require(len(password) >= 16, "Grafana password must be at least 16 characters")
        _require("\n" not in username and username, "Invalid Grafana administrator name")
        return username, password

    def restore_grafana_secret(self) -> None:
        existing = self._grafana_secret()
        if existing:
            raise LifecycleError("Existing malformed Grafana Secret requires manual review")
        username, password = self._grafana_credentials()
        manifest = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": GRAFANA_SECRET, "namespace": "platform-system"},
            "type": "Opaque",
            "stringData": {"admin-user": username, "admin-password": password},
        }
        self.run(
            ["kubectl", "create", "-f", "-"],
            data=json.dumps(manifest).encode(),
        )

    def observability_ready(self) -> bool:
        application = self._get_application("observability")
        if not _manual_sync(application) or not _application_ready(application):
            return False
        _require(
            len(application.get("spec", {}).get("sources", [])) == 3,
            "Observability Application is not the approved three-source definition",
        )
        workloads = []
        seen: set[tuple[str, str, str]] = set()
        # The Prometheus Operator owns StatefulSets with component-specific
        # instance labels; Helm release Deployments use instance=observability.
        for kind, expected_name, selector in OBSERVABILITY_WORKLOADS:
            matches = self.json([
                "kubectl", "-n", "platform-system", "get", kind,
                "-l", selector, "-o", "json",
            ]).get("items", [])
            if not isinstance(matches, list):
                raise LifecycleError("Invalid observability workload response")
            selected = [item for item in matches if
                        item.get("metadata", {}).get("name") == expected_name]
            if len(selected) != 1:
                if len(selected) > 1:
                    raise LifecycleError("Duplicate observability workload identity")
                return False
            item = selected[0]
            metadata = item.get("metadata", {})
            if (item.get("kind", "").lower() != kind or
                    metadata.get("namespace", "platform-system") != "platform-system" or
                    not metadata.get("uid")):
                raise LifecycleError("Observability workload identity mismatch")
            identity = ("platform-system", kind, expected_name)
            if identity in seen:
                raise LifecycleError("Duplicate observability workload identity")
            seen.add(identity)
            labels = metadata.get("labels", {})
            if kind == "statefulset":
                if (labels.get("app.kubernetes.io/managed-by") != "prometheus-operator" or
                        labels.get("release") != "observability"):
                    raise LifecycleError("Operator-managed workload ownership mismatch")
            elif labels.get("app.kubernetes.io/instance") != "observability":
                raise LifecycleError("Observability Deployment ownership mismatch")
            workloads.append(item)
        return all(_resource_ready(item) for item in workloads)

    def restore_observability(self) -> None:
        self._sync_application("observability", timeout=1800)
        self.run([
            "argocd", "app", "wait", "observability", "--sync", "--health",
            "--timeout", "1200",
        ], timeout=1260)

    def stage3_application_ready(self) -> bool:
        application = self._get_application("stage3-dev")
        source = application.get("spec", {}).get("source", {})
        if (
            source.get("targetRevision") != STAGE3_REVISION
            or not _manual_sync(application)
            or not _application_ready(application)
        ):
            return False
        deployments = [
            self.optional_json([
                "kubectl", "-n", "dev", "get", "deployment", name,
                "--ignore-not-found", "-o", "json",
            ])
            for name in ("eks-platform-backend", "eks-platform-frontend")
        ]
        images = {
            container.get("image", "")
            for deployment in deployments
            for container in deployment.get("spec", {}).get("template", {}).get(
                "spec", {}
            ).get("containers", [])
        }
        return (
            images == _expected_stage3_images()
            and len(deployments) == 2
            and all(_resource_ready(item) for item in deployments)
        )

    def restore_stage3_application(self) -> None:
        application = self._get_application("stage3-dev")
        _require(
            application.get("spec", {}).get("source", {}).get("targetRevision")
            == STAGE3_REVISION,
            "Refusing to synchronize an unreviewed Stage 3 revision",
        )
        self._sync_application(
            "stage3-dev", "--revision", STAGE3_REVISION, timeout=1200,
        )
        self.run([
            "argocd", "app", "wait", "stage3-dev", "--sync", "--health",
            "--timeout", "900",
        ], timeout=960)

    def run_stage(
        self,
        name: str,
        ready: Callable[[], bool],
        action: Callable[[], None],
    ) -> None:
        _require(name in PLATFORM_STAGES, "Unknown platform recovery stage")
        print(f"INFO: {name}: checking current state", flush=True)
        try:
            already_ready = ready()
        except Exception as exc:
            self.checkpoint.update(name, "failed", detail=type(exc).__name__)
            raise
        if already_ready:
            self.checkpoint.update(name, "passed", detail="Revalidated actual state")
            print(f"PASS: {name} already ready")
            return

        self.checkpoint.update(name, "running")
        print(f"INFO: {name}: starting approved recovery action", flush=True)
        try:
            action()
            _require(ready(), f"{name} action completed but acceptance failed")
            self.checkpoint.update(name, "passed")
            print(f"PASS: {name}")
        except Exception as exc:
            self.checkpoint.update(name, "failed", detail=type(exc).__name__)
            raise

    def recover(self) -> dict:
        validate_source_contract()
        if self.bootstrap:
            identity = self.bootstrap.prepare(auto_repair=True)
            reused = self.checkpoint.reconcile_identity(
                identity.checkpoint_identity()
            )
            state = "reused" if reused else "established"
            print(f"INFO: platform checkpoint identity {state}", flush=True)
        self.verify_identity(update_kubeconfig=self.bootstrap is None)
        actions = (
            ("argocd", self.argocd_ready, self.restore_argocd),
            (
                "gitops_dependencies",
                self.gitops_dependencies_ready,
                self.restore_gitops_dependencies,
            ),
            ("controllers", self.controllers_ready, self.restore_controllers),
            (
                "observability_crds",
                self.observability_crds_ready,
                self.restore_observability_crds,
            ),
            ("grafana_secret", self.grafana_secret_ready, self.restore_grafana_secret),
            ("observability", self.observability_ready, self.restore_observability),
        )
        self.run_stage(*actions[0])
        if self.bootstrap:
            with self.bootstrap.argocd_session():
                for name, ready, action in actions[1:]:
                    self.run_stage(name, ready, action)
        else:
            for name, ready, action in actions[1:]:
                self.run_stage(name, ready, action)

        release = self._argocd_release() or {}
        report = {
            "schema": 1,
            "timestamp_utc": utc_now(),
            "cluster": self.config.cluster_name,
            "argocd": {
                "chart": release.get("chart"),
                "revision": release.get("revision"),
                "status": release.get("status"),
                "controller_memory_request": "512Mi",
                "controller_memory_limit": "1Gi",
            },
            "manual_sync_preserved": True,
            "observability_crds": list(OBSERVABILITY_CRDS),
            "stage3_revision": STAGE3_REVISION,
            "stage3_images_immutable": "deferred_until_database_verification",
            "database_restore": "separate guarded stage",
        }
        atomic_json_write(
            self.config.evidence_dir / "python-platform-recovery.json",
            report,
        )
        return report


def platform_recovery_state_ready(config: LabConfig) -> bool:
    """Read live platform state without reconciling it."""
    validate_source_contract()
    manager = PlatformRecovery(
        config,
        CheckpointStore(
            config.evidence_dir / "python-platform-recovery-checkpoint.json"
        ),
    )
    manager.verify_identity(update_kubeconfig=False)
    checks = (
        manager.argocd_ready,
        manager.gitops_dependencies_ready,
        manager.controllers_ready,
        manager.observability_crds_ready,
        manager.grafana_secret_ready,
        manager.observability_ready,
    )
    for check in checks:
        if not check():
            return False
    return True


def validate_recovered_database(config: LabConfig) -> bool:
    """Verify RDS identity, Alembic head, schema and runtime-role protection."""
    account_guard(config)
    values, host, database = database_connection(config)
    instance = json.loads(output([
        "aws", "rds", "describe-db-instances", "--db-instance-identifier",
        config.cluster_name + "-postgres", "--output", "json",
    ], env=environment(config)))["DBInstances"][0]
    if (
        instance.get("DBInstanceStatus") != "available"
        or instance.get("Endpoint", {}).get("Address") != host
        or database != "app"
        or values.get("rds_address") != host
    ):
        return False

    pod_name = "recovery-db-verify-" + secrets.token_hex(5)
    pod = client_pod(config, pod_name)
    container = pod["spec"]["containers"][0]
    container["env"] = [
        {"name": "PGHOST", "value": host},
        {"name": "PGDATABASE", "value": database},
        {
            "name": "PGUSER",
            "valueFrom": {
                "secretKeyRef": {"name": "rds-credentials", "key": "username"}
            },
        },
        {
            "name": "PGPASSWORD",
            "valueFrom": {
                "secretKeyRef": {"name": "rds-credentials", "key": "password"}
            },
        },
        {"name": "PGSSLMODE", "value": "require"},
    ]
    query = (
        "SELECT (SELECT version_num='" + ALEMBIC_HEAD + "' FROM alembic_version),"
        "to_regclass('public.user') IS NOT NULL,to_regclass('public.item') IS NOT NULL,"
        "(SELECT data_type='uuid' FROM information_schema.columns WHERE table_schema='public' AND table_name='user' AND column_name='id'),"
        "(SELECT data_type='uuid' FROM information_schema.columns WHERE table_schema='public' AND table_name='item' AND column_name='id'),"
        "has_schema_privilege(current_user,'public','USAGE'),"
        "has_schema_privilege(current_user,'public','CREATE'),"
        + ",".join(
            "has_table_privilege(current_user,'\"public\".\"%s\"','%s')"
            % (table, privilege)
            for table in ("user", "item")
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
        ) + ";"
    )
    relation_count = (
        "SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relkind='r' "
        "AND c.relname IN ('alembic_version','user','item');"
    )
    container["command"] = [
        "sh",
        "-ec",
        "relations=$(psql -X -At -v ON_ERROR_STOP=1 -c \"$1\"); "
        "if [ \"$relations\" != 3 ]; then echo NOT_READY; exit 0; fi; "
        "result=$(psql -X -At -v ON_ERROR_STOP=1 -c \"$2\"); "
        "if [ \"$result\" = '" + "|".join(
            ("t",) * 6 + ("f",) + ("t",) * 8
        ) + "' ]; then "
        "echo 'PASS: Alembic, schema and app_runtime privileges verified'; "
        "else echo NOT_READY; fi",
        "verify",
        relation_count,
        query,
    ]
    created = False
    try:
        call(
            ["kubectl", "-n", "dev", "create", "-f", "-"],
            data=json.dumps(pod).encode(),
            env=environment(config),
        )
        created = True
        call([
            "kubectl", "-n", "dev", "wait",
            "--for=jsonpath={.status.phase}=Succeeded", "pod/" + pod_name,
            "--timeout=180s",
        ], env=environment(config), timeout=220)
        log = output([
            "kubectl", "-n", "dev", "logs", pod_name, "--tail=5",
        ], env=environment(config))
        return log.strip() == "PASS: Alembic, schema and app_runtime privileges verified"
    finally:
        if created:
            try:
                call([
                    "kubectl", "-n", "dev", "delete", "pod", pod_name,
                    "--ignore-not-found=true", "--wait=true", "--timeout=120s",
                ], env=environment(config), timeout=150)
            except LifecycleError:
                print(
                    "WARNING: Temporary database validator pod cleanup needs inspection",
                    file=sys.stderr,
                )


def validate_recovered_application(config: LabConfig) -> bool:
    """Verify recovered immutable workloads and FastAPI health endpoints."""
    manager = PlatformRecovery(
        config,
        CheckpointStore(
            config.evidence_dir / "python-platform-recovery-checkpoint.json"
        ),
    )
    if not manager.stage3_application_ready():
        return False
    script = (
        "import urllib.request; "
        "paths=('/health/live','/health/ready','/metrics'); "
        "assert all(urllib.request.urlopen('http://127.0.0.1:8000'+p,timeout=15).status==200 for p in paths); "
        "print('PASS')"
    )
    result = output([
        "kubectl", "-n", "dev", "exec", "deployment/eks-platform-backend",
        "--", "/workspace/.venv/bin/python", "-c", script,
    ], env=environment(config))
    return result.strip() == "PASS"


def main() -> None:
    config = LabConfig.from_environment()
    bootstrap = RecoveryBootstrap(config)
    manager = PlatformRecovery(
        config,
        CheckpointStore(
            config.evidence_dir / "python-platform-recovery-checkpoint.json"
        ),
        bootstrap=bootstrap,
    )
    report = manager.recover()
    print("PASS: Platform recovery completed")
    print("REPORT:", config.evidence_dir / "python-platform-recovery.json")
    print(json.dumps(report, indent=2))


def sanitized_failure(exc: Exception) -> str:
    """Return actionable diagnostics without emitting arbitrary exception data."""
    if not isinstance(exc, LifecycleError):
        return f"BLOCKED: {type(exc).__name__}"
    detail = " ".join(str(exc).split())
    detail = re.sub(
        r"(?i)\b(password|token|authorization|connection[-_ ]?string)\b"
        r"\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        detail,
    )
    detail = re.sub(r"(?i)postgres(?:ql)?://\S+", "[REDACTED_DSN]", detail)
    return f"BLOCKED: LifecycleError: {detail[:400]}"


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(sanitized_failure(exc), file=sys.stderr)
        sys.exit(1)
