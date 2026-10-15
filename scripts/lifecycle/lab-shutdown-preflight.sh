#!/usr/bin/env bash
# V3 safety: read-only shutdown inventory. NO destroy, cleanup, or approval.
set -Eeuo pipefail
umask 077
fail() { printf 'BLOCKED: %s\n' "$*" >&2; exit 78; }
for tool in aws terraform kubectl helm git python3; do
  command -v "$tool" >/dev/null 2>&1 || fail "Missing $tool"
done
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || fail 'Not a Git repository'
cd "$ROOT"
[[ "$(basename "$ROOT")" == aws-eks-platform-reliability-lab ]] || fail 'Unexpected repository'
export AWS_PROFILE="${AWS_PROFILE:-eks-lab-terraform}"
export AWS_REGION=eu-west-1 AWS_DEFAULT_REGION=eu-west-1 AWS_EC2_METADATA_DISABLED=true
ACCOUNT=275544662748
CLUSTER=eks-platform-reliability-lab
TF_DIR=infra/terraform/environments/lab
[[ "$(aws sts get-caller-identity --query Account --output text)" == "$ACCOUNT" ]] || fail 'Account mismatch'
[[ "$(terraform -chdir="$TF_DIR" workspace show)" == default ]] || fail 'Workspace mismatch'
[[ "$(terraform -chdir="$TF_DIR" output -raw cluster_name)" == "$CLUSTER" ]] || fail 'Cluster output mismatch'
[[ "$(kubectl config current-context)" == eks-reliability-lab ]] || fail 'Context mismatch'
EXPECTED_ARN="$(terraform -chdir="$TF_DIR" output -raw cluster_arn)"
[[ "$(aws eks describe-cluster --name "$CLUSTER" --query cluster.arn --output text)" == "$EXPECTED_ARN" ]] || fail 'Cluster ARN mismatch'
[[ "$(aws eks describe-cluster --name "$CLUSTER" --query cluster.status --output text)" == ACTIVE ]] || fail 'Cluster not ACTIVE'
[[ "$(aws rds describe-db-instances --db-instance-identifier "$CLUSTER-postgres" --query 'DBInstances[0].DBInstanceStatus' --output text)" == available ]] || fail 'RDS not available'
aws s3api head-bucket --bucket azawslab-eks-tfstate-275544662748-eu-west-1 --expected-bucket-owner "$ACCOUNT" >/dev/null || fail 'Permanent backend inaccessible'
aws secretsmanager describe-secret --secret-id "$CLUSTER/dev/app-runtime" --query ARN --output text >/dev/null || fail 'Retained application secret inaccessible'
# No assumption that a locally stored dump exists or was restored successfully.
OUT_DIR="$ROOT/.private-evidence/lifecycle"
mkdir -p "$OUT_DIR"
chmod 700 "$OUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="$OUT_DIR/shutdown-preflight-$STAMP.json"
TMP="$(mktemp -d "$OUT_DIR/.shutdown-preflight.XXXXXXXX")"
trap 'rm -rf -- "$TMP"' EXIT
# Every API query must succeed. Empty successful results are recorded, not treated as universal proof of absence.
kubectl get ingress -A -o json > "$TMP/ingress.json" || fail 'Ingress query failed'
kubectl get svc -A -o json > "$TMP/services.json" || fail 'Service query failed'
kubectl get pvc -A -o json > "$TMP/pvc.json" || fail 'PVC query failed'
kubectl get pv -o json > "$TMP/pv.json" || fail 'PV query failed'
kubectl get applications -n argocd -o json > "$TMP/applications.json" || fail 'Argo Applications query failed'
kubectl get externalsecrets.external-secrets.io -A -o json > "$TMP/externalsecrets.json" || fail 'ExternalSecret query failed'
kubectl get secretstores.external-secrets.io -A -o json > "$TMP/secretstores.json" || fail 'SecretStore query failed'
helm list -A -o json > "$TMP/helm.json" || fail 'Helm query failed'
VPC="$(terraform -chdir="$TF_DIR" output -raw vpc_id)" || fail 'VPC output missing'
aws elbv2 describe-load-balancers --output json > "$TMP/lb.json" || fail 'Load balancer query failed'
aws elbv2 describe-target-groups --output json > "$TMP/tg.json" || fail 'Target group query failed'
aws ec2 describe-volumes --filters "Name=tag:Project,Values=$CLUSTER" --output json > "$TMP/volumes.json" || fail 'EBS volume query failed'
aws ec2 describe-nat-gateways --filter "Name=vpc-id,Values=$VPC" --output json > "$TMP/nat.json" || fail 'NAT query failed'
python3 - "$TMP" "$REPORT" "$VPC" "$CLUSTER" <<'PY'
import json,sys,datetime,pathlib,os
src,dest,vpc,cluster=sys.argv[1:]
p=pathlib.Path(src)
def read(name): return json.loads((p/(name+'.json')).read_text())
lb=[{'name':x['LoadBalancerName'],'arn':x['LoadBalancerArn'],'type':x['Type']} for x in read('lb')['LoadBalancers'] if x.get('VpcId')==vpc]
tg=[{'name':x['TargetGroupName'],'arn':x['TargetGroupArn']} for x in read('tg')['TargetGroups'] if x.get('VpcId')==vpc]
counts={x:len(read(x).get('items',[])) for x in ['ingress','services','pvc','pv','applications','externalsecrets','secretstores']}
report={'timestamp_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'account_id':'275544662748','region':'eu-west-1','cluster_name':cluster,'vpc_id':vpc,'kubernetes_counts':counts,'helm_releases':[{'name':x['name'],'namespace':x['namespace']} for x in read('helm')],'vpc_load_balancers':lb,'vpc_target_groups':tg,'tagged_ebs_volume_count':len(read('volumes')['Volumes']),'nat_gateway_states':[{'id':x['NatGatewayId'],'state':x['State']} for x in read('nat')['NatGateways']],'database_backup_verified':False,'safe_to_destroy':False,'notes':['Read-only inventory only; no destructive actions.','Volume tag query is not an exhaustive EBS ownership inventory.','Database backup and restore proof must be checked separately.','Manual dependency cleanup, saved destroy-plan review and separate human approval required.']}
pathlib.Path(dest).write_text(json.dumps(report,indent=2)+'\n')
os.chmod(dest,0o600)
print('PASS: Read-only shutdown preflight report created:',dest)
print('Kubernetes object counts:',counts)
print('VPC load balancers:',len(lb),'target groups:',len(tg))
print('NOTE: safe_to_destroy=false; no cloud changes or destroy plan generated')
PY
