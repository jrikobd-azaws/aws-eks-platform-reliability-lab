#!/usr/bin/env bash
# Stage 2 lifecycle: read-only recovery inventory (NOT a database backup).
set -Eeuo pipefail
umask 077

fail() { printf 'BLOCKED: %s\n' "$*" >&2; exit 1; }
command -v aws >/dev/null || fail 'aws CLI missing'
command -v terraform >/dev/null || fail 'terraform missing'
command -v kubectl >/dev/null || fail 'kubectl missing'
command -v git >/dev/null || fail 'git missing'
command -v python3 >/dev/null || fail 'python3 missing'

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || fail 'Run inside the project Git repository'
cd "$ROOT"
[[ "$(basename "$ROOT")" == 'aws-eks-platform-reliability-lab' ]] || fail 'Unexpected repository directory'
EXPECTED_ACCOUNT_ID='275544662748'
EXPECTED_REGION='eu-west-1'
EXPECTED_CLUSTER='eks-platform-reliability-lab'
EXPECTED_CONTEXT='eks-reliability-lab'
EXPECTED_SECRET='eks-platform-reliability-lab/dev/app-runtime'
EXPECTED_BUCKET='azawslab-eks-tfstate-275544662748-eu-west-1'
export AWS_PROFILE="${AWS_PROFILE:-eks-lab-terraform}"
export AWS_REGION="$EXPECTED_REGION" AWS_DEFAULT_REGION="$EXPECTED_REGION" AWS_EC2_METADATA_DISABLED=true

[[ "$(aws sts get-caller-identity --query Account --output text)" == "$EXPECTED_ACCOUNT_ID" ]] || fail 'Wrong AWS account or expired authentication'
[[ "$(kubectl config current-context)" == "$EXPECTED_CONTEXT" ]] || fail 'Wrong Kubernetes context'
[[ "$(terraform -chdir=infra/terraform/environments/lab workspace show)" == 'default' ]] || fail 'Wrong Terraform workspace'
[[ "$(terraform -chdir=infra/terraform/environments/lab output -raw cluster_name)" == "$EXPECTED_CLUSTER" ]] || fail 'Wrong Terraform cluster output'
aws s3api head-bucket --bucket "$EXPECTED_BUCKET" --expected-bucket-owner "$EXPECTED_ACCOUNT_ID" --region "$EXPECTED_REGION" >/dev/null || fail 'State bucket not accessible'
secret_arn="$(aws secretsmanager describe-secret --secret-id "$EXPECTED_SECRET" --query ARN --output text)" || fail 'Retained secret not accessible'
cluster_arn="$(terraform -chdir=infra/terraform/environments/lab output -raw cluster_arn)" || fail 'Missing cluster ARN output'
[[ "$(aws eks describe-cluster --name "$EXPECTED_CLUSTER" --query cluster.arn --output text)" == "$cluster_arn" ]] || fail 'EKS cluster ARN mismatch'
[[ "$(aws eks describe-cluster --name "$EXPECTED_CLUSTER" --query cluster.status --output text)" == 'ACTIVE' ]] || fail 'EKS cluster not ACTIVE'
[[ "$(aws rds describe-db-instances --db-instance-identifier "${EXPECTED_CLUSTER}-postgres" --query 'DBInstances[0].DBInstanceStatus' --output text)" == 'available' ]] || fail 'RDS not available'

OUTDIR="$ROOT/.private-evidence/lifecycle"
mkdir -p "$OUTDIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUTFILE="$OUTDIR/recovery-inventory-$STAMP.json"
export INVENTORY_OUTPUT="$OUTFILE" INVENTORY_SECRET_ARN="$secret_arn" INVENTORY_CLUSTER_ARN="$cluster_arn"
python3 - <<'PY'
import json, os, subprocess, datetime

def run(*args):
    return subprocess.check_output(args, text=True).strip()

out = {
    'timestamp_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'account_id': '275544662748',
    'region': 'eu-west-1',
    'cluster_arn': os.environ['INVENTORY_CLUSTER_ARN'],
    'cluster_name': 'eks-platform-reliability-lab',
    'terraform_workspace': 'default',
    'terraform_outputs': {
        k: run('terraform', '-chdir=infra/terraform/environments/lab', 'output', '-raw', k)
        for k in ('vpc_id','rds_address','aws_load_balancer_controller_role_arn','dev_secrets_reader_role_arn')
    },
    'retained_application_secret_arn': os.environ['INVENTORY_SECRET_ARN'],
    'git_head': run('git','rev-parse','HEAD'),
    'git_branch': run('git','branch','--show-current'),
    'git_worktree_clean': not bool(run('git','status','--porcelain','--untracked-files=no')),
    'database_backup_included': False,
    'kubernetes_volume_backup_included': False,
    'safe_to_destroy': False,
}
with open(os.environ['INVENTORY_OUTPUT'], 'x', encoding='utf-8') as f:
    json.dump(out, f, indent=2)
    f.write('\n')
PY
chmod 600 "$OUTFILE"
printf 'PASS: Read-only recovery inventory saved to %s\n' "$OUTFILE"
printf 'NOTE: No database dump, PVC backup, cloud changes, or teardown approval performed.\n'
