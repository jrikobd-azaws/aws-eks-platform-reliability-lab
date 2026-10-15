#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
BRANCH="$(git branch --show-current)"
case "$BRANCH" in
  feature/stage3-*) ;;
  *)
    echo "BLOCKED: Unexpected Git branch: $BRANCH" >&2
    exit 78
    ;;
esac
[[ "$#" -le 1 && ( "$#" -eq 0 || "$1" == "--smoke-only" ) ]] || { echo 'Usage: build-stage3-local.sh [--smoke-only]' >&2; exit 2; }
command -v docker >/dev/null || { echo 'BLOCKED: Docker unavailable' >&2; exit 78; }
if [[ "${1:-}" != "--smoke-only" ]]; then
# First execution resolves digest references and writes an auditable lock file.
LOCK=app/base-images.lock
if [[ ! -f "$LOCK" ]]; then
  for spec in 'PYTHON_IMAGE python:3.14-slim' 'UV_IMAGE ghcr.io/astral-sh/uv:0.9.26' 'BUN_IMAGE oven/bun:1' 'NGINX_IMAGE nginxinc/nginx-unprivileged:stable-alpine'; do
    read -r name tag <<< "$spec"
    docker pull --platform linux/amd64 "$tag" >/dev/null
    pinned="$(docker image inspect "$tag" --format '{{index .RepoDigests 0}}')"
    [[ "$pinned" =~ @sha256:[0-9a-f]{64}$ ]] || { echo "BLOCKED: invalid digest for $name" >&2; exit 78; }
    printf '%s=%s\n' "$name" "$pinned" >> "$LOCK"
  done
  echo "REVIEW: generated $LOCK; commit reviewed digests for reproducibility"
fi
# Only source our own validated, fixed-format image lock file.
for key in PYTHON_IMAGE UV_IMAGE BUN_IMAGE NGINX_IMAGE; do
  line="$(grep -E "^${key}=" "$LOCK" || true)"
  [[ "$line" =~ ^${key}=[a-zA-Z0-9./:_-]+@sha256:[0-9a-f]{64}$ ]] || { echo "BLOCKED: invalid $key in $LOCK" >&2; exit 78; }
  export "$line"
done
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cp app/upstream/{uv.lock,bun.lock,pyproject.toml,package.json} "$TMP/"
cp -a app/backend app/frontend app/packages "$TMP/"
if find "$TMP" -type f -name '.env*' -print -quit | grep -q .; then echo 'BLOCKED: env file in build context' >&2; exit 78; fi
# Prevent source control and local artefacts from entering images.
find "$TMP" -type d \( -name .git -o -name node_modules -o -name .venv -o -name __pycache__ \) -prune -exec rm -rf {} +
cat > "$TMP/.dockerignore" <<'EOF'
**/.git
**/.env*
**/node_modules
**/.venv
**/__pycache__
**/.pytest_cache
**/tests
**/Dockerfile.playwright
EOF
docker buildx build --load --platform linux/amd64 --build-arg PYTHON_IMAGE="$PYTHON_IMAGE" --build-arg UV_IMAGE="$UV_IMAGE" -f "$TMP/backend/Dockerfile.eks" -t eks-lab-backend:local "$TMP"
docker buildx build --load --platform linux/amd64 --build-arg BUN_IMAGE="$BUN_IMAGE" --build-arg NGINX_IMAGE="$NGINX_IMAGE" -f "$TMP/frontend/Dockerfile.eks" -t eks-lab-frontend:local "$TMP"
fi
[[ "$(docker image inspect eks-lab-backend:local --format '{{.Config.User}}')" == "10001:10001" ]] || { echo 'FAIL: backend UID/GID' >&2; exit 1; }
[[ "$(docker image inspect eks-lab-frontend:local --format '{{.Config.User}}')" == "101" ]] || { echo 'FAIL: frontend UID' >&2; exit 1; }
echo '=== Verify non-root image identities ==='
docker image inspect eks-lab-backend:local --format 'backend user={{.Config.User}}'
docker image inspect eks-lab-frontend:local --format 'frontend user={{.Config.User}}'
echo '=== Runtime smoke tests ==='
NETWORK="stage3-batch-a-test-$$"
FRONTEND="stage3-frontend-$$"
BACKEND="stage3-backend-$$"
ENV_FILE="$(mktemp)"
chmod 600 "$ENV_FILE"
cleanup_runtime() {
  docker rm -f "$FRONTEND" "$BACKEND" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  rm -f "$ENV_FILE"
}
trap 'cleanup_runtime' EXIT
python3 - "$ENV_FILE" <<'PYENV'
import secrets
import sys
with open(sys.argv[1], 'w', encoding='utf-8') as f:
    f.write('PROJECT_NAME=EKS Reliability Lab\n')
    f.write('FIRST_SUPERUSER=test-admin@example.com\n')
    f.write('SECRET_KEY=' + secrets.token_urlsafe(48) + '\n')
    f.write('FIRST_SUPERUSER_PASSWORD=' + secrets.token_urlsafe(32) + '\n')
    f.write('DATABASE_URL=postgresql://test_user:test_password@127.0.0.1:5432/app\n')
PYENV
docker network create "$NETWORK" >/dev/null
docker run -d --name "$BACKEND" --network "$NETWORK" --network-alias backend --network-alias eks-platform-backend   --env-file "$ENV_FILE" --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m   -p 127.0.0.1:18081:8000 eks-lab-backend:local >/dev/null
docker run -d --name "$FRONTEND" --network "$NETWORK"   --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m   -p 127.0.0.1:18080:8080 eks-lab-frontend:local >/dev/null
wait_http() {
  local url="$1" expected="$2" actual="" name="$3"
  for attempt in $(seq 1 30); do
    actual="$(curl -sS --max-time 3 -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || true)"
    if [[ "$actual" == "$expected" ]]; then echo "PASS: $name HTTP $expected"; return 0; fi
    sleep 1
  done
  echo "FAIL: $name expected $expected, got $actual" >&2
  docker logs --tail 25 "$BACKEND" >&2 || true
  docker logs --tail 25 "$FRONTEND" >&2 || true
  return 1
}
wait_http http://127.0.0.1:18081/health/live 200 'backend liveness'
wait_http http://127.0.0.1:18081/metrics 200 'backend metrics'
wait_http http://127.0.0.1:18081/health/ready 503 'backend DB-negative readiness'
wait_http http://127.0.0.1:18080/ 200 'frontend SPA'
wait_http http://127.0.0.1:18080/api/v1/openapi.json 200 'frontend API proxy'
for item in "$BACKEND:10001" "$FRONTEND:101"; do
  container="${item%%:*}"; expected="${item##*:}"
  actual="$(docker exec "$container" id -u)"
  [[ "$actual" == "$expected" ]] || { echo "FAIL: UID for $container is $actual" >&2; exit 1; }
  echo "PASS: $container non-root UID $actual"
done
if command -v trivy >/dev/null 2>&1; then
  trivy image --exit-code 1 --severity HIGH,CRITICAL eks-lab-backend:local
  trivy image --exit-code 1 --severity HIGH,CRITICAL eks-lab-frontend:local
  echo 'PASS: Trivy image security scans'
else
  echo 'NOT VERIFIED: Trivy unavailable; image vulnerability scans required before Batch A sign-off'
fi
echo 'PASS: Local runtime smoke tests passed; live RDS readiness not tested.'
