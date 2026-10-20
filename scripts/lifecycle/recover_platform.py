#!/usr/bin/env python3
"""Stage 3 EKS platform recovery. Safe to retry after partial completion."""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TF = ROOT / "infra/terraform/environments/lab"
EVIDENCE = ROOT / ".private-evidence/lifecycle"
HELM_DIR = (
    EVIDENCE /
    "restore-implementation-20261016T012855Z/helm"
)
CLUSTER = "eks-platform-reliability-lab"
ACCOUNT = "275544662748"

os.environ.setdefault("AWS_PROFILE", "eks-lab-terraform")
os.environ["AWS_REGION"] = "eu-west-1"
os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"

def run(args, *, input_data=None, timeout=900):
    print("RUN:", " ".join(map(str, args)), flush=True)
    subprocess.run(
        list(map(str, args)),
        cwd=ROOT,
        input=input_data,
        text=True,
        check=True,
        timeout=timeout,
    )

def capture(args):
    return subprocess.check_output(
        list(map(str, args)),
        cwd=ROOT,
        text=True,
    ).strip()

def verify(condition, message):
    if not condition:
        raise RuntimeError(message)

def helm_version(name, prefix):
    value = (HELM_DIR / f"{name}-chart.txt").read_text().strip()
    verify(value.startswith(prefix), f"Unexpected chart: {value}")
    return value[len(prefix):]

def main():
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    verify(
        capture([
            "aws", "sts", "get-caller-identity",
            "--query", "Account", "--output", "text"
        ]) == ACCOUNT,
        "Wrong AWS account"
    )

    outputs = json.loads(capture([
        "terraform", f"-chdir={TF}", "output", "-json"
    ]))

    def output(name):
        return outputs[name]["value"]

    verify(output("cluster_name") == CLUSTER, "Wrong cluster")

    vpc = output("vpc_id")
    role = output("aws_load_balancer_controller_role_arn")
    oidc = output("cluster_oidc_issuer_url")
    rds = output("rds_address")

    print("PASS: Current Terraform outputs resolved")
    print("VPC:", vpc)
    print("OIDC:", oidc)
    print("RDS:", rds)

    run([
        "aws", "eks", "update-kubeconfig",
        "--name", CLUSTER,
        "--region", "eu-west-1",
        "--alias", "eks-reliability-lab"
    ])

    verify(
        capture(["kubectl", "config", "current-context"])
        == "eks-reliability-lab",
        "Unexpected Kubernetes context"
    )

    run([
        "kubectl", "wait", "nodes", "--all",
        "--for=condition=Ready", "--timeout=600s"
    ])

    for namespace in ("dev", "platform-system", "argocd"):
        manifest = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": namespace}
        }
        run(
            ["kubectl", "apply", "-f", "-"],
            input_data=json.dumps(manifest)
        )

    # Verify current IAM trust configuration for the recreated OIDC issuer.
    trust = json.loads(capture([
        "aws", "iam", "get-role",
        "--role-name",
        f"{CLUSTER}-aws-load-balancer-controller",
        "--query", "Role.AssumeRolePolicyDocument",
        "--output", "json"
    ]))

    verify(
        oidc.removeprefix("https://") in json.dumps(trust),
        "Load Balancer Controller IAM trust has stale OIDC issuer"
    )

    run([
        "helm", "repo", "add", "eks",
        "https://aws.github.io/eks-charts",
        "--force-update"
    ])

    run(["helm", "repo", "update"])

    version = helm_version(
        "aws-load-balancer-controller",
        "aws-load-balancer-controller-"
    )

    values = HELM_DIR / "aws-load-balancer-controller-values.yaml"
    verify(values.exists(), "Missing controller Helm values")

    run([
        "helm", "upgrade", "--install",
        "aws-load-balancer-controller",
        "eks/aws-load-balancer-controller",
        "--namespace", "kube-system",
        "--version", version,
        "-f", values,
        "--set-string", f"clusterName={CLUSTER}",
        "--set-string", f"region=eu-west-1",
        "--set-string", f"vpcId={vpc}",
        "--wait", "--timeout", "15m"
    ], timeout=1100)

    run([
        "kubectl", "-n", "kube-system",
        "rollout", "status",
        "deployment/aws-load-balancer-controller",
        "--timeout=300s"
    ])

    # Argo CD has already been installed. Reapply the root configuration.
    run(["kubectl", "apply", "-k", "gitops/argocd"])

    # Restore declarative secrets configuration.
    run([
        "kubectl", "apply", "-k",
        "gitops/platform/dev-secrets"
    ])

    # Reconciliation is asynchronous.
    for name in ("rds-credentials", "fastapi-auth"):
        deadline = time.monotonic() + 600

        while time.monotonic() < deadline:
            try:
                status = capture([
                    "kubectl", "-n", "dev",
                    "get", "externalsecret", name,
                    "-o",
                    "jsonpath={.status.conditions[?(@.type==\"Ready\")].status}"
                ])
                if status == "True":
                    break
            except subprocess.CalledProcessError:
                pass

            time.sleep(10)
        else:
            raise RuntimeError(f"ExternalSecret not Ready: {name}")

        print("PASS: ExternalSecret Ready:", name)

    run([
        "kubectl", "-n", "argocd",
        "get", "applications"
    ])

    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "cluster": CLUSTER,
        "vpc": vpc,
        "oidc": oidc,
        "rds_endpoint": rds,
        "load_balancer_controller": "validated",
        "argocd_bootstrap": "applied",
        "external_secrets": "ready",
        "postgresql_restore": "not_executed",
    }

    path = EVIDENCE / "python-platform-recovery.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    path.chmod(0o600)

    print("PASS: Platform recovery completed")
    print("REPORT:", path)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        sys.exit(1)
