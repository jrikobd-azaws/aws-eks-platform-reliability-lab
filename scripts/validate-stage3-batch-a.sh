#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
MODE="${1:-}"
[[ $# -eq 1 && ( "$MODE" == local || "$MODE" == rds || "$MODE" == fastapi-rds ) ]] || { echo 'Usage: bash scripts/validate-stage3-batch-a.sh {local|rds|fastapi-rds}' >&2; exit 64; }
fail() { echo "FAIL: $*" >&2; exit 1; }
BRANCH="$(git branch --show-current)"
case "$BRANCH" in
  feature/stage3-*) ;;
  *)
    fail "Unexpected Git branch: $BRANCH"
    ;;
esac

if [[ "$MODE" == local ]]; then
  command -v docker >/dev/null || fail 'Docker missing'
  command -v gitleaks >/dev/null || fail 'Gitleaks missing'
  echo '=== Static syntax and secret checks ==='
  bash -n scripts/build-stage3-local.sh
  python3 -m py_compile scripts/stage3-complete.py
  git diff --check
  gitleaks dir --no-banner app scripts/stage3-complete.py 2>/dev/null || {
    # Some gitleaks versions accept only one positional path.
    gitleaks dir --no-banner app || fail 'Gitleaks app scan failed'
    gitleaks dir --no-banner scripts || fail 'Gitleaks scripts scan failed'
  }
  echo '=== Runtime and image checks ==='
  bash scripts/build-stage3-local.sh --smoke-only
  for item in 'eks-lab-backend:local:10001' 'eks-lab-frontend:local:101'; do
    expected="${item##*:}"; img="${item%:*}"
    uid="$(docker image inspect "$img" --format '{{.Config.User}}')"
    [[ "${uid%%:*}" == "$expected" ]] || fail "Unexpected image user for $img: $uid"
    echo "PASS: $img image UID $uid"
  done
  echo '=== Vulnerability scan ==='
  if ! command -v trivy >/dev/null 2>&1; then
    echo 'BLOCKED: Trivy is not installed. No vulnerability-scan PASS claim.' >&2
    exit 78
  fi
  trivy image --scanners vuln --severity HIGH,CRITICAL --exit-code 1 eks-lab-backend:local
  trivy image --scanners vuln --severity HIGH,CRITICAL --exit-code 1 eks-lab-frontend:local
  echo 'PASS: Local smoke tests and vulnerability gate'
  exit 0
fi

if [[ "$MODE" == fastapi-rds ]]; then
  (
    set -Eeuo pipefail

    [[ "${LAB_FASTAPI_RDS_APPROVED:-}" == YES ]] || fail 'Set LAB_FASTAPI_RDS_APPROVED=YES'
    [[ "${AWS_PROFILE:-}" == eks-lab-terraform ]] || fail 'Wrong AWS profile'
    [[ "${AWS_REGION:-}" == eu-west-1 ]] || fail 'Wrong AWS region'

    for bin in aws kubectl terraform python3; do
      command -v "$bin" >/dev/null || fail "$bin missing"
    done

    ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
    [[ "$ACCOUNT" == 275544662748 ]] || fail 'Wrong AWS account'

    TF_DIR=infra/terraform/environments/lab
    CLUSTER="$(terraform -chdir="$TF_DIR" output -raw cluster_name)"
    ARN="$(terraform -chdir="$TF_DIR" output -raw cluster_arn)"
    HOST="$(terraform -chdir="$TF_DIR" output -raw rds_address)"
    DB="$(terraform -chdir="$TF_DIR" output -raw rds_database_name)"

    [[ "$CLUSTER" == eks-platform-reliability-lab ]] || fail 'Wrong cluster'
    [[ "$ARN" == arn:aws:eks:eu-west-1:275544662748:cluster/eks-platform-reliability-lab ]] || fail 'Wrong cluster ARN'
    [[ "$HOST" =~ ^[a-zA-Z0-9.-]+\.rds\.amazonaws\.com$ ]] || fail 'Invalid RDS hostname'
    [[ "$DB" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || fail 'Invalid database name'

    LIVE_ARN="$(aws eks describe-cluster --name "$CLUSTER" --query cluster.arn --output text)"
    [[ "$LIVE_ARN" == "$ARN" ]] || fail 'EKS ARN mismatch'

    SERVER="$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
    LIVE_SERVER="$(aws eks describe-cluster --name "$CLUSTER" --query cluster.endpoint --output text)"
    [[ "$SERVER" == "$LIVE_SERVER" ]] || fail 'Kubernetes context mismatch'

    for name in rds-credentials fastapi-auth; do
      [[ "$(kubectl -n dev get externalsecret "$name" -o jsonpath='{.status.conditions[0].status}')" == True ]] || fail "$name not synchronized"
      kubectl -n dev get secret "$name" >/dev/null || fail "$name Kubernetes Secret missing"
    done

    REPO="eks-platform-reliability-lab/backend"
    IMAGE="${LAB_BACKEND_IMAGE:-}"

    [[ "$IMAGE" =~ ^275544662748\.dkr\.ecr\.eu-west-1\.amazonaws\.com/eks-platform-reliability-lab/backend@sha256:[a-f0-9]{64}$ ]] || fail 'Provide immutable LAB_BACKEND_IMAGE'

    DIGEST="${IMAGE##*@}"
    aws ecr describe-images --repository-name "$REPO" \
      --image-ids "imageDigest=$DIGEST" >/dev/null || fail 'Image missing in ECR'

    POD="stage3-fastapi-rds-$(date +%s)-$$"

    # Execute temporary-pod cleanup directly on exit.
    trap '
      kubectl -n dev delete pod "$POD" \
        --ignore-not-found --wait=true --timeout=90s >/dev/null 2>&1 || \
        echo "WARNING: Verify temporary pod cleanup: $POD" >&2
    ' EXIT

    echo "=== CREATE TEMPORARY FASTAPI POD ==="

    cat <<YAML | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: $POD
  namespace: dev
  labels:
    app: stage3-fastapi-rds-validation
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
  securityContext:
    runAsNonRoot: true
    runAsUser: 10001
    runAsGroup: 10001
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: backend
      image: $IMAGE
      imagePullPolicy: IfNotPresent
      startupProbe:
        httpGet:
          path: /health/live
          port: 8000
        periodSeconds: 5
        timeoutSeconds: 3
        failureThreshold: 24
      livenessProbe:
        httpGet:
          path: /health/live
          port: 8000
        periodSeconds: 10
        timeoutSeconds: 3
        failureThreshold: 3
      readinessProbe:
        httpGet:
          path: /health/ready
          port: 8000
        periodSeconds: 5
        timeoutSeconds: 5
        failureThreshold: 3
      env:
        - name: DB_HOST
          value: "$HOST"
        - name: DB_NAME
          value: "$DB"
        - name: DB_USER
          valueFrom:
            secretKeyRef:
              name: rds-credentials
              key: username
        - name: DB_PASSWORD
          valueFrom:
            secretKeyRef:
              name: rds-credentials
              key: password
        - name: SECRET_KEY
          valueFrom:
            secretKeyRef:
              name: fastapi-auth
              key: SECRET_KEY
        - name: FIRST_SUPERUSER
          valueFrom:
            secretKeyRef:
              name: fastapi-auth
              key: FIRST_SUPERUSER
        - name: FIRST_SUPERUSER_PASSWORD
          valueFrom:
            secretKeyRef:
              name: fastapi-auth
              key: FIRST_SUPERUSER_PASSWORD
        - name: PROJECT_NAME
          value: "EKS Reliability Lab"
      command:
        - /workspace/.venv/bin/python
        - -c
        - |
          import os
          from urllib.parse import quote
          user = quote(os.environ.pop("DB_USER"), safe="")
          password = quote(os.environ.pop("DB_PASSWORD"), safe="")
          host = os.environ.pop("DB_HOST")
          db = os.environ.pop("DB_NAME")
          os.environ["DATABASE_URL"] = (
              f"postgresql://{user}:{password}@{host}:5432/{db}?sslmode=verify-full&sslrootcert=/etc/ssl/rds/global-bundle.pem"
          )
          os.execvp("fastapi", [
              "fastapi", "run", "--host", "0.0.0.0", "--port", "8000"
          ])
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities:
          drop: ["ALL"]
      resources:
        requests:
          cpu: 100m
          memory: 256Mi
        limits:
          cpu: 500m
          memory: 768Mi
YAML

    kubectl -n dev wait --for=condition=Ready \
      "pod/$POD" --timeout=180s || {
        kubectl -n dev get pod "$POD" -o wide >&2 || true
        fail 'FastAPI pod not Ready'
      }

    echo "=== APPLICATION STARTUP STATUS ==="

    kubectl -n dev get pod "$POD" -o wide

    echo "=== APPLICATION STARTUP LOGS ==="
    kubectl -n dev logs "$POD" -c backend --tail=40 || true

    echo "=== APPLICATION ENDPOINT TESTS ==="

    kubectl -n dev exec "$POD" -c backend -- \
      /workspace/.venv/bin/python -c '
import urllib.request
import urllib.error
import sys

checks = [
    ("/health/live", 200),
    ("/metrics", 200),
    ("/health/ready", 200),
]

for path, expected in checks:
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:8000" + path, timeout=15
        ) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except Exception as exc:
        print(f"FAIL: {path}: {type(exc).__name__}")
        sys.exit(1)

    if status != expected:
        print(f"FAIL: {path} HTTP {status}")
        sys.exit(1)

    print(f"PASS: {path} HTTP {status}")
'

    echo "PASS: FastAPI live RDS readiness validated"
    echo "PASS: PostgreSQL TLS verify-full configured; certificate chain and hostname verified through successful database readiness."
  )
  exit 0
fi

# RDS integration is an explicit Kubernetes write, not a local-only operation.
[[ "${AWS_PROFILE:-}" == eks-lab-terraform ]] || fail 'Set AWS_PROFILE=eks-lab-terraform'
[[ "${AWS_REGION:-}" == eu-west-1 ]] || fail 'Set AWS_REGION=eu-west-1'
for bin in aws kubectl terraform python3; do command -v "$bin" >/dev/null || fail "$bin missing"; done
[[ "$(aws sts get-caller-identity --query Account --output text)" == 275544662748 ]] || fail 'Wrong AWS account'
TF_DIR=infra/terraform/environments/lab
CLUSTER="$(terraform -chdir="$TF_DIR" output -raw cluster_name)"
ARN="$(terraform -chdir="$TF_DIR" output -raw cluster_arn)"
[[ "$CLUSTER" == eks-platform-reliability-lab ]] || fail 'Unexpected cluster name'
[[ "$ARN" == arn:aws:eks:eu-west-1:275544662748:cluster/eks-platform-reliability-lab ]] || fail 'Unexpected cluster ARN'
LIVE_ARN="$(aws eks describe-cluster --name "$CLUSTER" --region eu-west-1 --query cluster.arn --output text)"
[[ "$LIVE_ARN" == "$ARN" ]] || fail 'AWS cluster ARN mismatch'
SERVER="$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
AWS_SERVER="$(aws eks describe-cluster --name "$CLUSTER" --region eu-west-1 --query cluster.endpoint --output text)"
[[ "$SERVER" == "$AWS_SERVER" ]] || fail 'Kubeconfig endpoint mismatch'
[[ "$(kubectl -n dev get externalsecret rds-credentials -o jsonpath='{.status.conditions[0].status}')" == True ]] || fail 'ExternalSecret not Ready'
[[ "$(kubectl -n dev get secret rds-credentials -o jsonpath='{.type}')" == Opaque ]] || fail 'Credential Secret missing'
HOST="$(terraform -chdir="$TF_DIR" output -raw rds_address)"
DB="$(terraform -chdir="$TF_DIR" output -raw rds_database_name)"
[[ "$HOST" =~ ^[a-zA-Z0-9.-]+\.rds\.amazonaws\.com$ ]] || fail 'Unexpected RDS hostname'
[[ "$DB" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || fail 'Unexpected DB name'
[[ "${LAB_RDS_TEST_APPROVED:-}" == YES ]] || fail 'Set LAB_RDS_TEST_APPROVED=YES to authorise one temporary dev pod'
POD="stage3-rds-check-$(date +%s)-$$"
cleanup() { kubectl -n dev delete pod "$POD" --ignore-not-found --wait=false >/dev/null 2>&1 || true; }
trap cleanup EXIT
# No secret values pass through local process arguments or logs.
cat <<YAML | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: $POD
  namespace: dev
  labels:
    app: stage3-rds-validation
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
  securityContext:
    runAsNonRoot: true
    runAsUser: 65534
    runAsGroup: 65534
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: postgres-client
      image: postgres:16.15-alpine
      env:
        - name: PGHOST
          value: "$HOST"
        - name: PGDATABASE
          value: "$DB"
        - name: PGUSER
          valueFrom:
            secretKeyRef:
              name: rds-credentials
              key: username
        - name: PGPASSWORD
          valueFrom:
            secretKeyRef:
              name: rds-credentials
              key: password
        - name: PGSSLMODE
          value: require
      command:
        - sh
        - -ec
        - |
          result=\$(psql -X -A -t -v ON_ERROR_STOP=1 -c "SELECT current_user, (SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()), has_schema_privilege(current_user, 'public', 'CREATE');")
          test "\$result" = 'app_runtime|t|f'
          echo 'PASS: app_runtime authenticated over TLS without CREATE privilege'
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities:
          drop: ["ALL"]
      resources:
        requests:
          cpu: 25m
          memory: 32Mi
        limits:
          cpu: 100m
          memory: 128Mi
YAML
if ! kubectl -n dev wait --for=jsonpath='{.status.phase}'=Succeeded "pod/$POD" --timeout=180s; then
  kubectl -n dev get pod "$POD" -o wide >&2 || true
  kubectl -n dev logs "$POD" --tail=30 >&2 || true
  fail 'RDS integration test failed'
fi
kubectl -n dev logs "$POD" --tail=5
echo 'PASS: Kubernetes Secret -> PostgreSQL authentication/TLS test. FastAPI-to-RDS still unverified.'
