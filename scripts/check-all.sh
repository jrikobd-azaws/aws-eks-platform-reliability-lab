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
if [[ -f gitops/platform/kustomization.yaml ]]; then
  python3 -c 'import yaml' >/dev/null 2>&1 || { printf '%s\n' 'BLOCKED: PyYAML is required for platform manifest validation.' >&2; exit 78; }
  python3 scripts/validate-platform.py
fi
if find infra/terraform -type f -name '*.tf' -print -quit 2>/dev/null | grep -q .; then
  command -v terraform >/dev/null 2>&1 || { printf '%s\n' 'BLOCKED: terraform not installed for local static validation.' >&2; exit 78; }
  terraform fmt -check -diff -recursive infra/terraform
  for root in infra/terraform/permanent infra/terraform/fixture infra/terraform/environments/lab; do
    if [[ -d "$root" ]]; then
      tmp_tf_data_dir="$(mktemp -d)"
      cleanup() {
        if [[ -n "${tmp_tf_data_dir:-}" && -d "$tmp_tf_data_dir" ]]; then
          rm -rf "$tmp_tf_data_dir"
        fi
      }
      trap cleanup RETURN
      TF_DATA_DIR="$tmp_tf_data_dir" terraform -chdir="$root" init -backend=false -input=false
      TF_DATA_DIR="$tmp_tf_data_dir" terraform -chdir="$root" validate
      trap - RETURN
      cleanup
    fi
  done
  if command -v trivy >/dev/null 2>&1; then
    trivy config --severity HIGH,CRITICAL infra/terraform
  else
    printf '%s\n' 'Trivy not installed; skipping IaC security scan.'
  fi
fi
if [[ -f app/helm/Chart.yaml ]]; then
 command -v helm >/dev/null || exit 78
 helm lint app/helm --set-string backend.image=offline.invalid/backend:validation --set-string frontend.image=offline.invalid/frontend:validation
 helm template safety-check app/helm --set-string backend.image=offline.invalid/backend:validation --set-string frontend.image=offline.invalid/frontend:validation >/dev/null
fi
printf '%s\n' 'Implemented local checks passed. No cloud-validation claim.'
