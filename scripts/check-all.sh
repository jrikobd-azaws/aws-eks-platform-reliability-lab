#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for tool in python3 shellcheck gitleaks; do
 command -v "$tool" >/dev/null 2>&1 || { printf 'BLOCKED: missing required tool %s\n' "$tool" >&2; exit 78; }
done
mapfile -d '' scripts < <(find scripts -type f -name '*.sh' -print0)
for script in "${scripts[@]}"; do bash -n "$script"; done
shellcheck -x -P SCRIPTDIR "${scripts[@]}"
python3 scripts/check-v3-patch.py
python3 scripts/check_scenarios.py
gitleaks dir --no-banner .
if find infra/terraform -type f -name '*.tf' -print -quit 2>/dev/null | grep -q .; then
 printf '%s\n' 'BLOCKED: configure and run per-root Terraform validation/lint/security gates.' >&2
 exit 78
fi
if [[ -f app/helm/Chart.yaml ]]; then
 command -v helm >/dev/null || exit 78
 helm lint app/helm
 helm template safety-check app/helm >/dev/null
fi
printf '%s\n' 'Implemented local checks passed. No cloud-validation claim.'
