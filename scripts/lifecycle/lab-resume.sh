#!/usr/bin/env bash
# AWS EKS lab recovery: safe preflight and saved create plan only.
# No AWS/Kubernetes writes, Terraform apply, or database restore.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF_DIR="$ROOT/infra/terraform/environments/lab"
EXPECTED_ACCOUNT=275544662748
REGION=eu-west-1
CLUSTER=eks-platform-reliability-lab
SECRET_NAME="$CLUSTER/dev/app-runtime"
BACKEND_BUCKET="azawslab-eks-tfstate-${EXPECTED_ACCOUNT}-${REGION}"
PROFILE="${AWS_PROFILE:-eks-lab-terraform}"
BACKUP_ROOT="$HOME/.local/share/eks-lab-backups/postgres"
KEY="$HOME/.config/eks-lab/age-backup-key.txt"

fail() { printf 'BLOCKED: %s\n' "$*" >&2; exit 78; }
need() { command -v "$1" >/dev/null 2>&1 || fail "Missing $1"; }
usage() { printf 'Usage: AWS_PROFILE=eks-lab-terraform bash scripts/lifecycle/lab-resume.sh {preflight|plan}\n'; }
[[ $# -eq 1 ]] || { usage; exit 2; }
case "$1" in preflight|plan) MODE="$1" ;; *) usage; exit 2 ;; esac
for tool in aws terraform python3 git age age-inspect pg_restore sha256sum; do
  if [[ "$tool" == age-inspect ]]; then continue; fi
  need "$tool"
done
[[ -d "$TF_DIR" ]] || fail 'Terraform lab root missing'
[[ -s "$KEY" ]] || fail 'age private key missing'
[[ "$PROFILE" == 'eks-lab-terraform' ]] || fail 'Use approved Terraform profile'
export AWS_PROFILE="$PROFILE" AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" AWS_EC2_METADATA_DISABLED=true
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)" || fail 'AWS STS unavailable; authenticate manually'
[[ "$ACCOUNT" == "$EXPECTED_ACCOUNT" ]] || fail "Wrong AWS account: $ACCOUNT"
aws s3api head-bucket --bucket "$BACKEND_BUCKET" --expected-bucket-owner "$EXPECTED_ACCOUNT" --region "$REGION" >/dev/null || fail 'Permanent state bucket unavailable'
SECRET_ARN="$(aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --query ARN --output text)" || fail 'Retained application secret unavailable'
[[ "$SECRET_ARN" == arn:aws:secretsmanager:${REGION}:${EXPECTED_ACCOUNT}:secret:${SECRET_NAME}-* ]] || fail 'Unexpected application secret ARN'

# Only check encrypted file integrity here. Never decrypt to disk or display data.
shopt -s nullglob
backups=("$BACKUP_ROOT"/*.dump.age)
((${#backups[@]} > 0)) || fail 'No encrypted database backup found'
LATEST="$(printf '%s\n' "${backups[@]}" | sort | tail -n 1)"
[[ -s "$LATEST" ]] || fail 'Backup empty'
# Decrypt to pg_restore stdin; listing the archive is not a database restore test.
age -d -i "$KEY" "$LATEST" | pg_restore --list >/dev/null || fail 'Backup decryption/archive inspection failed'
SHA="$(sha256sum "$LATEST" | awk '{print $1}')"
[[ -n "$SHA" ]] || fail 'SHA256 failed'
printf 'PASS: Account %s, backend and retained secret verified\n' "$ACCOUNT"
printf 'PASS: Encrypted archive readable: %s\nSHA256: %s\n' "$LATEST" "$SHA"

# Never change backend initialization automatically. It must already be configured.
WORKSPACE="$(terraform -chdir="$TF_DIR" workspace show)" || fail 'Terraform backend unavailable'
[[ "$WORKSPACE" == default ]] || fail "Unexpected workspace: $WORKSPACE"
STATE="$(terraform -chdir="$TF_DIR" state list)" || fail 'Terraform state unavailable'
if [[ -n "$STATE" ]]; then
  printf 'INFO: Terraform state is populated; do not plan a rebuild over the running lab.\n'
  [[ "$MODE" == preflight ]] || fail 'Rebuild plan requires empty lab state'
  printf 'PASS: Resume preflight completed (running environment). No resources changed.\n'
  exit 0
fi
printf 'PASS: Lab Terraform state empty; rebuild planning is eligible.\n'
[[ "$MODE" == plan ]] || { printf 'PASS: Resume preflight completed. No resources changed.\n'; exit 0; }

# Verify no same-named cluster/RDS exists outside Terraform state.
if aws eks describe-cluster --name "$CLUSTER" --region "$REGION" >/dev/null 2>&1; then
  fail 'EKS cluster exists but is absent from Terraform state'
else
  RC=$?
  [[ "$RC" -eq 254 ]] || fail 'EKS lookup failed unexpectedly; inspect manually'
  ERR="$(aws eks describe-cluster --name "$CLUSTER" --region "$REGION" 2>&1)" || true
  [[ "$ERR" == *ResourceNotFoundException* ]] || fail 'EKS absence not established'
fi
RDS_ID="${CLUSTER}-postgres"
if aws rds describe-db-instances --db-instance-identifier "$RDS_ID" --region "$REGION" >/dev/null 2>&1; then
  fail 'RDS instance exists but is absent from Terraform state'
else
  ERR="$(aws rds describe-db-instances --db-instance-identifier "$RDS_ID" --region "$REGION" 2>&1)" || true
  [[ "$ERR" == *DBInstanceNotFound* ]] || fail 'RDS absence not established'
fi

# Inputs are drawn from approved values and existing configuration. The public endpoint CIDR
# cannot be safely inferred; require an explicit current approved /32.
: "${LAB_APPROVED_ENDPOINT_CIDR:?Set LAB_APPROVED_ENDPOINT_CIDR to the current approved IPv4 /32}"
python3 - "$LAB_APPROVED_ENDPOINT_CIDR" <<'PY'
import ipaddress,sys
try:
    net=ipaddress.ip_network(sys.argv[1], strict=True)
    assert isinstance(net, ipaddress.IPv4Network) and net.prefixlen==32
except Exception:
    raise SystemExit('BLOCKED: Expected an approved IPv4 /32')
PY
export TF_VAR_expected_account_id="$EXPECTED_ACCOUNT"
export TF_VAR_cluster_admin_principal_arn="arn:aws:iam::${EXPECTED_ACCOUNT}:user/azawslab-admin"
export TF_VAR_cluster_endpoint_public_access_cidrs="[\"${LAB_APPROVED_ENDPOINT_CIDR}\"]"
export TF_VAR_db_engine_version=16.15
export TF_VAR_app_runtime_secret_arn="$SECRET_ARN"
PLAN_DIR="$ROOT/.private-evidence/lifecycle/resume-plan-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$PLAN_DIR"
chmod 700 "$PLAN_DIR"
terraform -chdir="$TF_DIR" validate >/dev/null || fail 'Terraform validate failed'
terraform -chdir="$TF_DIR" plan -input=false -lock-timeout=60s -out="$PLAN_DIR/rebuild.tfplan" || fail 'Terraform create plan failed'
terraform -chdir="$TF_DIR" show -no-color "$PLAN_DIR/rebuild.tfplan" | grep -E '^  # |^Plan:' || true
printf 'PLAN ONLY: %s/rebuild.tfplan\n' "$PLAN_DIR"
printf 'No Terraform apply, Helm, Kubernetes writes, or database restoration performed.\n'
