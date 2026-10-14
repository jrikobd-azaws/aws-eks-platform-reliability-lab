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
  command -v terraform >/dev/null 2>&1 || { printf '%s\n' 'BLOCKED: terraform not installed for local static validation.' >&2; exit 78; }
  for root in infra/terraform/permanent infra/terraform/fixture infra/terraform/environments/lab; do
    if [[ -d "$root" ]]; then
      terraform -chdir="$root" fmt -check -diff -recursive
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
      if command -v trivy >/dev/null 2>&1; then
        trivy config --severity HIGH,CRITICAL "$root"
      fi
    fi
  done
fi
if [[ -f app/helm/Chart.yaml ]]; then
 command -v helm >/dev/null || exit 78
 helm lint app/helm
 helm template safety-check app/helm >/dev/null
fi
printf '%s\n' 'Implemented local checks passed. No cloud-validation claim.'
