#!/usr/bin/env bash
# V3 candidate: controlled shutdown. NOT approved for live execution until reviewed and tested.
# Run from VS Code WSL: bash scripts/lifecycle/lab-shutdown.sh --preflight
# Human-approved live run: bash scripts/lifecycle/lab-shutdown.sh --execute
set -Eeuo pipefail
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$ROOT"
ACCOUNT=275544662748
REGION=eu-west-1
CLUSTER=eks-platform-reliability-lab
BUCKET=azawslab-eks-tfstate-275544662748-eu-west-1
TF_DIR=infra/terraform/environments/lab
export AWS_PROFILE="${AWS_PROFILE:-eks-lab-terraform}" AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" AWS_EC2_METADATA_DISABLED=true
fail() { printf 'BLOCKED: %s\n' "$*" >&2; exit 78; }
for tool in aws terraform kubectl helm python3 age pg_restore; do command -v "$tool" >/dev/null || fail "Missing $tool"; done
[[ "$#" -eq 1 && ( "$1" == --preflight || "$1" == --execute ) ]] || fail 'Usage: lab-shutdown.sh --preflight|--execute'
MODE="$1"
[[ "$(basename "$ROOT")" == aws-eks-platform-reliability-lab ]] || fail 'Wrong repository'
[[ "$(aws sts get-caller-identity --query Account --output text)" == "$ACCOUNT" ]] || fail 'Wrong AWS account or expired login; use aws login --profile eks-lab-admin'
[[ "$(terraform -chdir="$TF_DIR" workspace show)" == default ]] || fail 'Wrong Terraform workspace'
[[ "$(kubectl config current-context)" == eks-reliability-lab ]] || fail 'Wrong kubectl context'
[[ "$(terraform -chdir="$TF_DIR" output -raw cluster_name)" == "$CLUSTER" ]] || fail 'Wrong Terraform cluster'
[[ "$(aws eks describe-cluster --name "$CLUSTER" --query cluster.arn --output text)" == "$(terraform -chdir="$TF_DIR" output -raw cluster_arn)" ]] || fail 'Cluster ARN mismatch'
aws s3api head-bucket --bucket "$BUCKET" --expected-bucket-owner "$ACCOUNT" >/dev/null || fail 'Permanent backend inaccessible'
SECRET_ARN="$(aws secretsmanager describe-secret --secret-id "$CLUSTER/dev/app-runtime" --query ARN --output text)" || fail 'Retained secret inaccessible'
[[ "$SECRET_ARN" == "arn:aws:secretsmanager:$REGION:$ACCOUNT:secret:$CLUSTER/dev/app-runtime-"* ]] || fail 'Retained secret ARN mismatch'
for f in scripts/lifecycle/lab-backup.sh scripts/lifecycle/lab-db-backup.sh scripts/lifecycle/lab-shutdown-preflight.sh scripts/lifecycle/lab-shutdown-plan.sh; do [[ -f "$f" ]] || fail "Missing $f"; bash -n "$f" || fail "Invalid $f"; done
printf 'PASS: Identity, context, backend, secret and component checks\n'
bash scripts/lifecycle/lab-shutdown-preflight.sh
if [[ "$MODE" == --preflight ]]; then
  printf 'PREFLIGHT ONLY: no backups, Kubernetes mutations, or destroy operations performed.\n'
  exit 0
fi
[[ -t 0 ]] || fail 'Interactive terminal required'
cat <<'NOTICE'
LIVE SHUTDOWN CANDIDATE: Requires explicit human approval.
Will back up the database, suspend GitOps, remove only named lab GitOps objects,
uninstall three known Helm releases, create/review a saved Terraform destroy plan,
then (after separate confirmation) destroy the disposable Terraform lab stack.
Do not use while application Ingress, volumes or external load balancers exist.
NOTICE
read -r -p 'Type BACKUP-AND-CLEANUP to approve these changes: ' approval
[[ "$approval" == BACKUP-AND-CLEANUP ]] || fail 'Not approved'
bash scripts/lifecycle/lab-backup.sh
LAB_DB_BACKUP_APPROVED=YES bash scripts/lifecycle/lab-db-backup.sh --execute
# The planning component checks LB/TGB/PV/PVC/Ingress dependencies before any teardown.
# Generate the saved plan BEFORE cleanup, but re-plan after cleanup to avoid stale state.
bash scripts/lifecycle/lab-shutdown-plan.sh --plan
# Verify only expected Application and Helm releases exist, not unknown owners.
python3 - <<'PY' || fail 'Unexpected Argo applications or Helm releases'
import json, subprocess
apps=json.loads(subprocess.check_output(['kubectl','get','applications','-n','argocd','-o','json']))
names={x['metadata']['name'] for x in apps['items']}
assert names == {'platform-root','aws-load-balancer-controller','external-secrets','dev-secrets'}, names
for ns, expected in [('argocd',{'argocd'}),('kube-system',{'aws-load-balancer-controller'}),('platform-system',{'external-secrets'})]:
    data=json.loads(subprocess.check_output(['helm','list','-n',ns,'-o','json']))
    found={x['name'] for x in data}
    assert found == expected, (ns,found)
print('PASS: Named application and Helm inventory matches approved baseline')
PY
# Suspend root reconciliation first; remove Application CRs without deleting Helm-owned resources.
kubectl -n argocd patch application platform-root --type merge -p '{"spec":{"syncPolicy":null}}'
for app in platform-root dev-secrets aws-load-balancer-controller external-secrets; do
  kubectl -n argocd delete application "$app" --cascade=orphan --wait=true
 done
# Explicitly delete only known dev secrets integration resources; never print Secret data.
kubectl -n dev delete externalsecret rds-credentials --wait=true
kubectl -n dev delete secretstore aws-secrets-manager --wait=true
kubectl -n dev delete serviceaccount external-secrets-reader --wait=true
# Kubernetes Secret owned by ExternalSecret may be deleted automatically; verify no retained secret.
if kubectl -n dev get secret rds-credentials >/dev/null 2>&1; then fail 'Generated dev Secret remains; inspect before continuing'; fi
# No controller is removed before dependencies have been checked.
helm uninstall external-secrets -n platform-system --wait --timeout 10m
helm uninstall aws-load-balancer-controller -n kube-system --wait --timeout 10m
helm uninstall argocd -n argocd --wait --timeout 10m
# Re-run fresh inventory and create a fresh saved plan after cleanup.
bash scripts/lifecycle/lab-shutdown-plan.sh --plan
printf '\nFind the newest saved plan under .private-evidence/lifecycle/destroy-plan-*/lab-destroy.tfplan.\n'
LATEST="$(find "$ROOT/.private-evidence/lifecycle" -mindepth 2 -maxdepth 2 -name lab-destroy.tfplan -type f -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"
[[ -n "$LATEST" && -s "$LATEST" ]] || fail 'No saved plan'
# Restrict planned actions to destroy-only, and require a nonzero count.
terraform -chdir="$TF_DIR" show -json "$LATEST" | python3 -c '
import json,sys
p=json.load(sys.stdin); changes=p.get("resource_changes",[])
acts=[(r["address"],r["change"]["actions"]) for r in changes if r["change"]["actions"]!=["no-op"]]
assert acts and all(a==["delete"] for _,a in acts), "Non-destroy or empty plan"
assert all(a.startswith(("module.eks.","module.network.","module.rds.","module.platform_iam.")) for a,_ in acts), "Unexpected root resource"
print("PASS: destroy-only plan with",len(acts),"approved-module resources")
' || fail 'Plan actions outside expected scope'
printf '\nReview full private plan before approval: terraform -chdir=%s show -no-color %q\n' "$TF_DIR" "$LATEST"
read -r -p 'Type DESTROY-48-LAB-RESOURCES only after reviewing the FULL plan: ' confirm
[[ "$confirm" == DESTROY-48-LAB-RESOURCES ]] || fail 'Destroy not approved'
# Ensure actual plan count remains exactly the previously reviewed lab inventory.
terraform -chdir="$TF_DIR" show -json "$LATEST" | python3 -c 'import json,sys; p=json.load(sys.stdin); a=[r for r in p["resource_changes"] if r["change"]["actions"]==["delete"]]; assert len(a)==48, "Expected exactly 48 deletes"' || fail 'Unexpected destroy count'
terraform -chdir="$TF_DIR" apply -input=false "$LATEST"
# Verify critical deletions and permanent resource retention. Errors are UNKNOWN, never success.
if aws eks describe-cluster --name "$CLUSTER" >/dev/null 2>&1; then fail 'EKS still exists'; fi
if aws rds describe-db-instances --db-instance-identifier "$CLUSTER-postgres" >/dev/null 2>&1; then fail 'RDS still exists'; fi
[[ -z "$(terraform -chdir="$TF_DIR" state list)" ]] || fail 'Terraform state not empty'
aws s3api head-bucket --bucket "$BUCKET" --expected-bucket-owner "$ACCOUNT" >/dev/null || fail 'Backend missing'
aws secretsmanager describe-secret --secret-id "$SECRET_ARN" >/dev/null || fail 'Application secret missing'
printf 'PARTIAL PASS: Terraform state empty, EKS/RDS queries did not succeed, retained resources accessible.\n'
printf 'REQUIRED: Independently verify EKS/RDS NotFound error codes, VPC/NAT/EIP/ENI and residual billing resources.\n'
