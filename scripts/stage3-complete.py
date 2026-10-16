#!/usr/bin/env python3
"""One-time Stage 3 Batch A source adaptation, idempotent; no cloud operations."""
from pathlib import Path
import subprocess
import sys

root = Path.cwd()
if not (root/'docs/app-baseline.md').is_file() or not (root/'app/upstream/uv.lock').is_file():
    sys.exit('BLOCKED: run from the EKS lab repository with imported upstream source')
branch = subprocess.check_output(['git','branch','--show-current'], text=True).strip()
if branch != 'feature/stage3-application-delivery':
    sys.exit(f'BLOCKED: unexpected branch {branch}')

def patch(path, old, new):
    p=root/path
    s=p.read_text()
    if new in s: return
    if path == 'app/backend/app/main.py' and 'app.frontend(' not in s and 'install_platform_health' not in s:
        marker='app.include_router(api_router, prefix=settings.API_V1_STR)'
        if s.count(marker)==1:
            p.write_text(s.replace(marker, marker+'\n\n'+new))
            return
    if s.count(old)!=1: sys.exit(f'BLOCKED: expected one source match in {path}')
    p.write_text(s.replace(old,new))

def write(path, content):
    p=root/path
    if p.exists() and p.read_text()!=content:
        old=p.read_text()
        signatures={
          'app/backend/Dockerfile.eks': 'ARG PYTHON_IMAGE=python:3.14-slim',
          'app/frontend/Dockerfile.eks': 'ARG BUN_IMAGE=oven/bun:1',
          'app/frontend/nginx.eks.conf': 'proxy_pass http://backend.dev.svc.cluster.local:8000;',
          'scripts/build-stage3-local.sh': 'ROOT="$(cd',
        }
        if signatures.get(path) not in old:
            sys.exit(f'BLOCKED: unexpected existing file: {path}; refusing overwrite')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)

patch('app/backend/app/main.py', 'return f"{route.tags[0]}-{route.name}"', 'return f"{route.tags[0] if route.tags else \'platform\'}-{route.name}"')
patch('app/frontend/vite.config.ts','outDir: "../backend/app/frontend",','outDir: "dist",')
patch('app/backend/app/main.py','app.frontend("/", directory=FRONTEND_DIR)', '''# Standalone frontend is served by Nginx in a separate container.
from app.platform_health import install_platform_health
install_platform_health(app)''')
write('app/backend/app/platform_health.py', '''"""Small internal platform endpoints; do not expose metrics through public ingress."""
from fastapi import FastAPI, Response
from sqlalchemy import text
from app.core.db import engine


def install_platform_health(app: FastAPI) -> None:
    @app.get('/health/live', include_in_schema=False)
    def live() -> dict[str, str]:
        return {'status': 'alive'}

    @app.get('/health/ready', include_in_schema=False)
    def ready(response: Response) -> dict[str, str]:
        try:
            with engine.connect() as connection:
                connection.execute(text('SELECT 1'))
        except Exception:
            response.status_code = 503
            return {'status': 'unready'}
        return {'status': 'ready'}

    @app.get('/metrics', include_in_schema=False)
    def metrics() -> Response:
        # Minimal Prometheus-compatible platform metric; no user/credential labels.
        return Response('# HELP eks_lab_application_up Application process running\\n'
                        '# TYPE eks_lab_application_up gauge\\n'
                        'eks_lab_application_up 1\\n',
                        media_type='text/plain; version=0.0.4; charset=utf-8')
''')
write('app/backend/Dockerfile.eks', '''# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE
ARG UV_IMAGE
FROM ${UV_IMAGE} AS uvbin
FROM ${PYTHON_IMAGE}
COPY --from=uvbin /uv /uvx /usr/local/bin/
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /workspace
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/pyproject.toml
RUN uv sync --frozen --no-dev --package app --no-install-project
COPY backend/app backend/app
COPY backend/alembic.ini backend/alembic.ini
RUN uv sync --frozen --no-dev --package app && \\
    useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser && \\
    chown -R 10001:10001 /workspace
USER 10001:10001
WORKDIR /workspace/backend
ENV PATH=/workspace/.venv/bin:$PATH
EXPOSE 8000
CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
''')
write('app/frontend/Dockerfile.eks', '''# syntax=docker/dockerfile:1.7
ARG BUN_IMAGE
ARG NGINX_IMAGE
FROM ${BUN_IMAGE} AS builder
WORKDIR /workspace
COPY package.json bun.lock ./
COPY frontend/package.json frontend/package.json
COPY packages packages
RUN bun install --frozen-lockfile
COPY frontend frontend
WORKDIR /workspace/frontend
ENV VITE_API_URL=
RUN bun run build && test -f dist/index.html
FROM ${NGINX_IMAGE} AS runtime
COPY --from=builder /workspace/frontend/dist /usr/share/nginx/html
COPY frontend/nginx.eks.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
''')
write('app/frontend/nginx.eks.conf', '''server {
    listen 8080;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;
    location = /health/live { access_log off; add_header Content-Type text/plain; return 200 "ok"; }
    location /api/ {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
    location / { try_files $uri $uri/ /index.html; }
}
''')
write('scripts/build-stage3-local.sh', '#!/usr/bin/env bash\nset -euo pipefail\nROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"\ncd "$ROOT"\n[[ "$(git branch --show-current)" == feature/stage3-application-delivery ]] || { echo \'BLOCKED: wrong branch\' >&2; exit 78; }\n[[ "$#" -le 1 && ( "$#" -eq 0 || "$1" == "--smoke-only" ) ]] || { echo \'Usage: build-stage3-local.sh [--smoke-only]\' >&2; exit 2; }\ncommand -v docker >/dev/null || { echo \'BLOCKED: Docker unavailable\' >&2; exit 78; }\nif [[ "${1:-}" != "--smoke-only" ]]; then\n# First execution resolves digest references and writes an auditable lock file.\nLOCK=app/base-images.lock\nif [[ ! -f "$LOCK" ]]; then\n  for spec in \'PYTHON_IMAGE python:3.14-slim\' \'UV_IMAGE ghcr.io/astral-sh/uv:0.9.26\' \'BUN_IMAGE oven/bun:1\' \'NGINX_IMAGE nginxinc/nginx-unprivileged:stable-alpine\'; do\n    read -r name tag <<< "$spec"\n    docker pull --platform linux/amd64 "$tag" >/dev/null\n    pinned="$(docker image inspect "$tag" --format \'{{index .RepoDigests 0}}\')"\n    [[ "$pinned" =~ @sha256:[0-9a-f]{64}$ ]] || { echo "BLOCKED: invalid digest for $name" >&2; exit 78; }\n    printf \'%s=%s\\n\' "$name" "$pinned" >> "$LOCK"\n  done\n  echo "REVIEW: generated $LOCK; commit reviewed digests for reproducibility"\nfi\n# Only source our own validated, fixed-format image lock file.\nfor key in PYTHON_IMAGE UV_IMAGE BUN_IMAGE NGINX_IMAGE; do\n  line="$(grep -E "^${key}=" "$LOCK" || true)"\n  [[ "$line" =~ ^${key}=[a-zA-Z0-9./:_-]+@sha256:[0-9a-f]{64}$ ]] || { echo "BLOCKED: invalid $key in $LOCK" >&2; exit 78; }\n  export "$line"\ndone\nTMP="$(mktemp -d)"\ntrap \'rm -rf "$TMP"\' EXIT\ncp app/upstream/{uv.lock,bun.lock,pyproject.toml,package.json} "$TMP/"\ncp -a app/backend app/frontend app/packages "$TMP/"\nif find "$TMP" -type f -name \'.env*\' -print -quit | grep -q .; then echo \'BLOCKED: env file in build context\' >&2; exit 78; fi\n# Prevent source control and local artefacts from entering images.\nfind "$TMP" -type d \\( -name .git -o -name node_modules -o -name .venv -o -name __pycache__ \\) -prune -exec rm -rf {} +\ncat > "$TMP/.dockerignore" <<\'EOF\'\n**/.git\n**/.env*\n**/node_modules\n**/.venv\n**/__pycache__\n**/.pytest_cache\n**/tests\n**/Dockerfile.playwright\nEOF\ndocker buildx build --load --platform linux/amd64 --build-arg PYTHON_IMAGE="$PYTHON_IMAGE" --build-arg UV_IMAGE="$UV_IMAGE" -f "$TMP/backend/Dockerfile.eks" -t eks-lab-backend:local "$TMP"\ndocker buildx build --load --platform linux/amd64 --build-arg BUN_IMAGE="$BUN_IMAGE" --build-arg NGINX_IMAGE="$NGINX_IMAGE" -f "$TMP/frontend/Dockerfile.eks" -t eks-lab-frontend:local "$TMP"\nfi\n[[ "$(docker image inspect eks-lab-backend:local --format \'{{.Config.User}}\')" == "10001:10001" ]] || { echo \'FAIL: backend UID/GID\' >&2; exit 1; }\n[[ "$(docker image inspect eks-lab-frontend:local --format \'{{.Config.User}}\')" == "101" ]] || { echo \'FAIL: frontend UID\' >&2; exit 1; }\necho \'=== Verify non-root image identities ===\' \ndocker image inspect eks-lab-backend:local --format \'backend user={{.Config.User}}\'\ndocker image inspect eks-lab-frontend:local --format \'frontend user={{.Config.User}}\'\necho \'=== Runtime smoke tests ===\'\nNETWORK="stage3-batch-a-test-$$"\nFRONTEND="stage3-frontend-$$"\nBACKEND="stage3-backend-$$"\nENV_FILE="$(mktemp)"\nchmod 600 "$ENV_FILE"\ncleanup_runtime() {\n  docker rm -f "$FRONTEND" "$BACKEND" >/dev/null 2>&1 || true\n  docker network rm "$NETWORK" >/dev/null 2>&1 || true\n  rm -f "$ENV_FILE"\n}\ntrap \'cleanup_runtime\' EXIT\npython3 - "$ENV_FILE" <<\'PYENV\'\nimport secrets\nimport sys\nwith open(sys.argv[1], \'w\', encoding=\'utf-8\') as f:\n    f.write(\'PROJECT_NAME=EKS Reliability Lab\\n\')\n    f.write(\'FIRST_SUPERUSER=test-admin@example.com\\n\')\n    f.write(\'SECRET_KEY=\' + secrets.token_urlsafe(48) + \'\\n\')\n    f.write(\'FIRST_SUPERUSER_PASSWORD=\' + secrets.token_urlsafe(32) + \'\\n\')\n    f.write(\'DATABASE_URL=postgresql://test_user:test_password@127.0.0.1:5432/app\\n\')\nPYENV\ndocker network create "$NETWORK" >/dev/null\ndocker run -d --name "$BACKEND" --network "$NETWORK" --network-alias backend   --env-file "$ENV_FILE" --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m   -p 127.0.0.1:18081:8000 eks-lab-backend:local >/dev/null\ndocker run -d --name "$FRONTEND" --network "$NETWORK"   --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m   -p 127.0.0.1:18080:8080 eks-lab-frontend:local >/dev/null\nwait_http() {\n  local url="$1" expected="$2" actual="" name="$3"\n  for attempt in $(seq 1 30); do\n    actual="$(curl -sS --max-time 3 -o /dev/null -w \'%{http_code}\' "$url" 2>/dev/null || true)"\n    if [[ "$actual" == "$expected" ]]; then echo "PASS: $name HTTP $expected"; return 0; fi\n    sleep 1\n  done\n  echo "FAIL: $name expected $expected, got $actual" >&2\n  docker logs --tail 25 "$BACKEND" >&2 || true\n  docker logs --tail 25 "$FRONTEND" >&2 || true\n  return 1\n}\nwait_http http://127.0.0.1:18081/health/live 200 \'backend liveness\'\nwait_http http://127.0.0.1:18081/metrics 200 \'backend metrics\'\nwait_http http://127.0.0.1:18081/health/ready 503 \'backend DB-negative readiness\'\nwait_http http://127.0.0.1:18080/ 200 \'frontend SPA\'\nwait_http http://127.0.0.1:18080/api/v1/openapi.json 200 \'frontend API proxy\'\nfor item in "$BACKEND:10001" "$FRONTEND:101"; do\n  container="${item%%:*}"; expected="${item##*:}"\n  actual="$(docker exec "$container" id -u)"\n  [[ "$actual" == "$expected" ]] || { echo "FAIL: UID for $container is $actual" >&2; exit 1; }\n  echo "PASS: $container non-root UID $actual"\ndone\nif command -v trivy >/dev/null 2>&1; then\n  trivy image --exit-code 1 --severity HIGH,CRITICAL eks-lab-backend:local\n  trivy image --exit-code 1 --severity HIGH,CRITICAL eks-lab-frontend:local\n  echo \'PASS: Trivy image security scans\'\nelse\n  echo \'NOT VERIFIED: Trivy unavailable; image vulnerability scans required before Batch A sign-off\'\nfi\necho \'PASS: Local runtime smoke tests passed; live RDS readiness not tested.\'\n')
print('PASS: Stage 3 source adaptation and automated build/runtime validation generated. No Docker builds or AWS changes executed.')
