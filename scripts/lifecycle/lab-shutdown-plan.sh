#!/usr/bin/env bash
# V3 lifecycle: read-only dependency gate and saved Terraform destroy PLAN.
# This script NEVER executes Terraform apply, Helm uninstall, kubectl delete or cloud writes.
set -Eeuo pipefail
umask 077
fail() { printf 'BLOCKED: %s\n' "$*" >&2; exit 78; }
[[ "${1:-}" == --plan && "$#" -eq 1 ]] || fail 'Usage: bash scripts/lifecycle/lab-shutdown-plan.sh --plan'
for tool in aws terraform kubectl python3 age pg_restore sha256sum git; do
  command -v "$tool" >/dev/null 2>&1 || fail "Missing required tool: $tool"
done
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || fail 'Not inside a Git repository'
[[ "$(basename "$ROOT")" == aws-eks-platform-reliability-lab ]] || fail 'Unexpected repository'
cd "$ROOT"
export AWS_PROFILE="${AWS_PROFILE:-eks-lab-terraform}"
export AWS_REGION=eu-west-1 AWS_DEFAULT_REGION=eu-west-1 AWS_EC2_METADATA_DISABLED=true
ACCOUNT=275544662748
CLUSTER=eks-platform-reliability-lab
TF_DIR=infra/terraform/environments/lab
BUCKET=azawslab-eks-tfstate-275544662748-eu-west-1
[[ "$(aws sts get-caller-identity --query Account --output text)" == "$ACCOUNT" ]] || fail 'Wrong AWS account'
[[ "$(terraform -chdir="$TF_DIR" workspace show)" == default ]] || fail 'Wrong Terraform workspace'
[[ "$(terraform -chdir="$TF_DIR" output -raw cluster_name)" == "$CLUSTER" ]] || fail 'Wrong cluster in Terraform outputs'
[[ "$(kubectl config current-context)" == eks-reliability-lab ]] || fail 'Wrong kubectl context'
ARN="$(terraform -chdir="$TF_DIR" output -raw cluster_arn)"
[[ "$(aws eks describe-cluster --name "$CLUSTER" --query cluster.arn --output text)" == "$ARN" ]] || fail 'EKS ARN mismatch'
aws s3api head-bucket --bucket "$BUCKET" --expected-bucket-owner "$ACCOUNT" >/dev/null || fail 'State backend inaccessible'
aws secretsmanager describe-secret --secret-id "$CLUSTER/dev/app-runtime" --query ARN --output text >/dev/null || fail 'Retained app secret inaccessible'
# Fail closed if application storage or externally exposed resources remain.
OUT="$ROOT/.private-evidence/lifecycle"
mkdir -p "$OUT" && chmod 700 "$OUT"
TMP="$(mktemp -d "$OUT/.plan-preflight.XXXXXXXX")"
trap 'rm -rf -- "$TMP"' EXIT
kubectl get ingress -A -o json > "$TMP/ingress.json" || fail 'Ingress inventory failed'
kubectl get svc -A -o json > "$TMP/svc.json" || fail 'Service inventory failed'
kubectl get pvc -A -o json > "$TMP/pvc.json" || fail 'PVC inventory failed'
kubectl get pv -o json > "$TMP/pv.json" || fail 'PV inventory failed'
kubectl get targetgroupbindings.elbv2.k8s.aws -A -o json > "$TMP/tgb.json" || fail 'TargetGroupBinding inventory failed'
VPC="$(terraform -chdir="$TF_DIR" output -raw vpc_id)"
aws elbv2 describe-load-balancers --output json > "$TMP/lb.json" || fail 'ALB/NLB inventory failed'
aws elbv2 describe-target-groups --output json > "$TMP/tg.json" || fail 'Target-group inventory failed'
python3 - "$TMP" "$VPC" <<'PY' || fail 'Dependent workload/storage/load balancer resources remain'
import json, pathlib, sys
p=pathlib.Path(sys.argv[1]); vpc=sys.argv[2]
def read(n): return json.loads((p/(n+'.json')).read_text())
counts={n:len(read(n)['items']) for n in ('ingress','pvc','pv','tgb')}
counts['external_services']=sum(x.get('spec',{}).get('type')=='LoadBalancer' for x in read('svc')['items'])
counts['vpc_load_balancers']=sum(x.get('VpcId')==vpc for x in read('lb')['LoadBalancers'])
counts['vpc_target_groups']=sum(x.get('VpcId')==vpc for x in read('tg')['TargetGroups'])
print('Dependency inventory:', counts)
if any(counts.values()): sys.exit(1)
PY
# Verify the latest encrypted local backup is readable; do not print contents.
KEY="$HOME/.config/eks-lab/age-backup-key.txt"
BACKUP_DIR="$HOME/.local/share/eks-lab-backups/postgres"
[[ -s "$KEY" ]] || fail 'Age private key unavailable'
[[ -d "$BACKUP_DIR" ]] || fail 'Database backup directory missing'
shopt -s nullglob
backups=("$BACKUP_DIR"/*.dump.age)
((${#backups[@]})) || fail 'No encrypted database backup found'
LATEST="$(printf '%s\n' "${backups[@]}" | LC_ALL=C sort | tail -n 1)"
[[ -s "$LATEST" ]] || fail 'Latest database backup empty'
age -d -i "$KEY" "$LATEST" | pg_restore --list >/dev/null || fail 'Encrypted database archive verification failed'
sha256sum "$LATEST" > "$TMP/backup.sha256"
# Private directory contains a saved plan and its reviewable summary; never commit either.
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$OUT/destroy-plan-$STAMP"
mkdir -m 700 "$DEST"
printf 'Verified encrypted backup: %s\n' "$LATEST"
printf 'Saved plan directory: %s\n' "$DEST"
terraform -chdir="$TF_DIR" show -json > "$TMP/state.json" || fail 'State unavailable'
python3 - "$TMP/state.json" "$TMP/inputs.tfvars.json" "$ACCOUNT" "$CLUSTER" <<'PYVARS' || fail 'Cannot derive required inputs'
import json,sys,pathlib
state=json.load(open(sys.argv[1])); account=sys.argv[3]; cluster=sys.argv[4]
items=[]
def walk(m):
    items.extend(m.get('resources',[]))
    for c in m.get('child_modules',[]): walk(c)
walk(state['values']['root_module'])
def one(t,n):
    matches=[r['values'] for r in items if r.get('type')==t and r.get('name')==n]
    if len(matches)!=1: raise ValueError(f'Expected one {t}.{n}, got {len(matches)}')
    return matches[0]
eks=one('aws_eks_cluster','this'); rds=one('aws_db_instance','this'); access=one('aws_eks_access_entry','human_operator')
assert eks['name']==cluster
principal=access['principal_arn']
assert principal.startswith((f'arn:aws:iam::{account}:user/',f'arn:aws:iam::{account}:role/'))
cidrs=eks['vpc_config'][0]['public_access_cidrs']
assert cidrs and all(x.endswith('/32') for x in cidrs)
assert rds['engine_version']=='16.15'
values={'expected_account_id':account,'cluster_admin_principal_arn':principal,'cluster_endpoint_public_access_cidrs':cidrs,'db_engine_version':'16.15'}
p=pathlib.Path(sys.argv[2]);p.write_text(json.dumps(values));p.chmod(0o600)
print('PASS: Required variables derived from existing Terraform state')
PYVARS
SECRET_ARN="$(aws secretsmanager describe-secret --secret-id "$CLUSTER/dev/app-runtime" --query ARN --output text)" || fail 'Secret lookup failed'
[[ "$SECRET_ARN" == "arn:aws:secretsmanager:eu-west-1:$ACCOUNT:secret:$CLUSTER/dev/app-runtime-"* ]] || fail 'Unexpected secret ARN'
python3 - "$TMP/inputs.tfvars.json" "$SECRET_ARN" <<'PYSECRET' || fail 'Cannot prepare temporary tfvars'
import json,pathlib,sys
p=pathlib.Path(sys.argv[1]);v=json.loads(p.read_text());v['app_runtime_secret_arn']=sys.argv[2];p.write_text(json.dumps(v));p.chmod(0o600)
PYSECRET
terraform -chdir="$TF_DIR" plan -destroy -input=false -lock-timeout=60s -var-file="$TMP/inputs.tfvars.json" -out="$DEST/lab-destroy.tfplan" || fail 'Terraform destroy planning failed; NO apply performed'
terraform -chdir="$TF_DIR" show -no-color "$DEST/lab-destroy.tfplan" > "$DEST/plan-review.txt" || fail 'Unable to render saved plan'
python3 - "$DEST/plan-review.txt" <<'PY'
import pathlib,re,sys
s=pathlib.Path(sys.argv[1]).read_text()
for line in s.splitlines():
    if re.match(r'^  # |^Plan:',line): print(line)
PY
printf '\nPLAN ONLY: No resources changed. Review full private plan before separate human approval.\n'
printf 'Permanent backend and application secret must remain untouched.\n'
