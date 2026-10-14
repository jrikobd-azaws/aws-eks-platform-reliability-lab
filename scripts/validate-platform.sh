#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

for tool in aws kubectl terraform python3; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'BLOCKED: required read-only validator tool %s is unavailable.\n' "$tool" >&2
    exit 78
  }
done

: "${EXPECTED_ACCOUNT_ID:?Set the verified workload account ID.}"
: "${AWS_REGION:?Set AWS_REGION to eu-west-1.}"
[[ "$AWS_REGION" == "eu-west-1" ]] || {
  printf 'FAIL: AWS_REGION must be eu-west-1.\n' >&2
  exit 1
}

tf_dir="infra/terraform/environments/lab"
actual_account="$(aws sts get-caller-identity --query Account --output text)"
[[ "$actual_account" == "$EXPECTED_ACCOUNT_ID" ]] || {
  printf 'FAIL: active AWS account does not match EXPECTED_ACCOUNT_ID.\n' >&2
  exit 1
}

cluster_name="$(terraform -chdir="$tf_dir" output -raw cluster_name)"
expected_arn="$(terraform -chdir="$tf_dir" output -raw cluster_arn)"
cluster_json="$(aws eks describe-cluster --region "$AWS_REGION" --name "$cluster_name" --output json)"
actual_arn="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["cluster"]["arn"])' <<<"$cluster_json")"
actual_endpoint="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["cluster"]["endpoint"])' <<<"$cluster_json")"
actual_ca="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["cluster"]["certificateAuthority"]["data"])' <<<"$cluster_json")"
[[ "$actual_arn" == "$expected_arn" ]] || {
  printf 'FAIL: live EKS cluster ARN differs from Terraform output.\n' >&2
  exit 1
}

kube_endpoint="$(kubectl config view --raw --minify --output jsonpath='{.clusters[0].cluster.server}')"
kube_ca="$(kubectl config view --raw --minify --output jsonpath='{.clusters[0].cluster.certificate-authority-data}')"
[[ "$kube_endpoint" == "$actual_endpoint" && "$kube_ca" == "$actual_ca" ]] || {
  printf 'FAIL: current kubectl context endpoint or CA differs from the exact EKS cluster.\n' >&2
  exit 1
}

kubectl get nodes -o json | python3 -c '
import json, sys
nodes = json.load(sys.stdin)["items"]
ready = [node for node in nodes if any(
    condition.get("type") == "Ready" and condition.get("status") == "True"
    for condition in node.get("status", {}).get("conditions", [])
)]
if len(nodes) != 2 or len(ready) != 2:
    raise SystemExit(f"FAIL: expected exactly two Ready nodes; found {len(ready)} Ready of {len(nodes)}.")
'

kubectl get namespace argocd platform-system dev >/dev/null
kubectl get storageclass gp3 -o json | python3 -c '
import json, sys
storage = json.load(sys.stdin)
annotations = storage.get("metadata", {}).get("annotations", {})
if storage.get("provisioner") != "ebs.csi.aws.com":
    raise SystemExit("FAIL: gp3 uses the wrong provisioner.")
if storage.get("parameters", {}).get("type") != "gp3":
    raise SystemExit("FAIL: gp3 storage class is not gp3.")
if annotations.get("storageclass.kubernetes.io/is-default-class") != "false":
    raise SystemExit("FAIL: gp3 must remain non-default until the old default is reviewed.")
'

kubectl rollout status deployment/argocd-server -n argocd --timeout=120s
kubectl rollout status deployment/aws-load-balancer-controller -n kube-system --timeout=120s
kubectl rollout status deployment/external-secrets -n platform-system --timeout=120s
kubectl get applications.argoproj.io -n argocd -o json | python3 -c '
import json, sys
applications = json.load(sys.stdin)["items"]
required = {"platform-root", "aws-load-balancer-controller", "external-secrets", "dev-secrets"}
names = {application["metadata"]["name"] for application in applications}
missing = sorted(required - names)
if missing:
    raise SystemExit("FAIL: required Argo Applications missing: " + ", ".join(missing))
for application in applications:
  name = application["metadata"]["name"]
  if name in required:
        sync = application.get("status", {}).get("sync", {}).get("status")
        health = application.get("status", {}).get("health", {}).get("status")
        if sync != "Synced" or health != "Healthy":
      raise SystemExit(f"FAIL: {name} is {sync}/{health}.")
'

kubectl get externalsecret rds-credentials -n dev -o json | python3 -c '
import json, sys
external_secret = json.load(sys.stdin)
conditions = external_secret.get("status", {}).get("conditions", [])
if not any(condition.get("type") == "Ready" and condition.get("status") == "True" for condition in conditions):
    raise SystemExit("FAIL: dev RDS ExternalSecret is not Ready.")
'
kubectl get secret rds-credentials -n dev -o name >/dev/null
printf 'PASS: exact EKS identity, nodes, namespaces, gp3, controllers, Argo apps and ExternalSecret readiness verified.\n'