# Stage 1B infrastructure lifecycle — human-operated, not automated

`scripts/start.sh`, `stop.sh`, `validate-up.sh` and `validate-down.sh` remain intentionally blocked. This runbook describes commands for a human operator; it is not evidence of a tested deploy/rebuild/teardown. The Terraform root is `infra/terraform/environments/lab`; it uses the existing S3 backend with the separate key `lab/terraform.tfstate` and never manages the permanent stack.

## Preconditions

Before any human-run plan/apply, confirm from AWS documentation/console and the exact workload account that EKS 1.36 is supported in `eu-west-1`, all four EKS add-ons are available for that version, the selected `t3.medium` node group and PostgreSQL class/version are orderable, quotas and budget are adequate, and `10.30.0.0/16` does not overlap connected networks. The Terraform plan resolves the most recent region-compatible add-on versions and checks the RDS class/version orderability; it is not a substitute for quota, cost, or architecture review. Supply a narrow public endpoint CIDR and the exact approved human execution role ARN. Never use `0.0.0.0/0` for the Kubernetes API.

The provider enforces `allowed_account_ids`; still verify the active identity and Region before every plan/apply. Keep state bucket, account ID, operator role, endpoint CIDR and engine version outside Git. Terraform does not create or modify the permanent state bucket, budget, GitHub OIDC provider, or plan role.
This stack does not create ECR repositories; confirm the exact permanent repository names and ownership before Stage 1C image publishing. No ECR resources or existing permanent resources are modified here.

## Deploy or rebuild

Run from the repository root in the human-controlled AWS terminal. Values in angle brackets must be replaced with verified values; do not copy examples as credentials.

```bash
set -euo pipefail
umask 077
export AWS_REGION=eu-west-1 AWS_DEFAULT_REGION=eu-west-1
export EXPECTED_ACCOUNT_ID='<verified 12-digit account ID>'
export TF_STATE_BUCKET='<exact existing permanent state bucket name>'
export TF_VAR_expected_account_id="$EXPECTED_ACCOUNT_ID"
export TF_VAR_cluster_admin_principal_arn='arn:aws:iam::<account-id>:user/<approved-human-user-or-role>'
export TF_VAR_cluster_endpoint_public_access_cidrs='["<trusted-public-ip>/32"]'
export TF_VAR_db_engine_version='<verified orderable PostgreSQL minor version>'
test "$(aws sts get-caller-identity --query Account --output text)" = "$EXPECTED_ACCOUNT_ID"
test "$AWS_REGION" = eu-west-1
terraform -chdir=infra/terraform/environments/lab init -reconfigure -input=false -backend-config="bucket=$TF_STATE_BUCKET"
test "$(terraform -chdir=infra/terraform/environments/lab workspace show)" = default
terraform -chdir=infra/terraform/environments/lab validate
terraform -chdir=infra/terraform/environments/lab plan -out="$HOME/stage-1b.tfplan"
terraform -chdir=infra/terraform/environments/lab show "$HOME/stage-1b.tfplan"
```

Inspect the complete saved plan, exact account/region/backend/workspace, expected resource count, subnet/CIDR ranges, public API CIDR, add-on versions, instance class, IAM policies and destroy/replacement actions. Store the plan securely; it may contain sensitive values. Only the human may approve and run:

```bash
terraform -chdir=infra/terraform/environments/lab apply "$HOME/stage-1b.tfplan"
```

For a rebuild after a verified destroy, repeat identity checks, backend initialization, validation, plan review and the separately approved apply. Never reuse a stale saved plan. The S3 state bucket and `lab/terraform.tfstate` key are retained; do not remove or edit state manually.

## Dependency and validation order

Terraform creates the VPC and two-AZ subnets/routes first, then cluster IAM/security/logging and the EKS control plane. The graph then branches: the compatible VPC CNI add-on precedes the managed node group, while the private RDS subnet group/security group/instance can proceed once the network and node security group exist. CoreDNS, kube-proxy and EBS CSI add-ons wait for the managed node group. RDS ingress accepts PostgreSQL only from the managed node group security group. Terraform's graph handles resource ordering, but a saved plan must still be reviewed.

After apply, compare the exact AWS cluster ARN to the Terraform output, configure a named kubeconfig context, and verify two Ready managed nodes and add-on health. Keep command output and private raw evidence out of Git:

```bash
terraform -chdir=infra/terraform/environments/lab output
aws eks describe-cluster --region eu-west-1 --name "$(terraform -chdir=infra/terraform/environments/lab output -raw cluster_name)" --query 'cluster.{arn:arn,status:status,version:version,endpoint:endpoint}'
ACTUAL_CLUSTER_ARN="$(aws eks describe-cluster --region eu-west-1 --name "$(terraform -chdir=infra/terraform/environments/lab output -raw cluster_name)" --query 'cluster.arn' --output text)"
test "$ACTUAL_CLUSTER_ARN" = "$(terraform -chdir=infra/terraform/environments/lab output -raw cluster_arn)"
aws eks update-kubeconfig --region eu-west-1 --name "$(terraform -chdir=infra/terraform/environments/lab output -raw cluster_name)" --alias stage-1b-lab
kubectl get nodes -o wide
kubectl get pods -n kube-system -o wide
for addon in vpc-cni coredns kube-proxy aws-ebs-csi-driver; do
	aws eks describe-addon --region eu-west-1 --cluster-name "$(terraform -chdir=infra/terraform/environments/lab output -raw cluster_name)" --addon-name "$addon" --query 'addon.{name:addonName,status:status,version:addonVersion}'
done
aws rds describe-db-instances --region eu-west-1 --db-instance-identifier "$(terraform -chdir=infra/terraform/environments/lab output -raw cluster_name)-postgres" --query 'DBInstances[0].{status:DBInstanceStatus,public:PubliclyAccessible,encrypted:StorageEncrypted,endpoint:Endpoint.Address}'
```

Do not claim infra validation based only on `terraform validate`; record actual cluster/node/add-on/RDS readiness and exact IDs in private evidence. An absent resource is proven only by successful service API queries; access denied, timeout, malformed output or missing tools means **UNKNOWN**, not absent.

## Safe teardown

Before teardown, export required evidence privately and verify account, Region, backend, workspace, cluster ARN and captured resource IDs again. Once application/platform layers exist, suspend GitOps reconciliation safely, remove only identified lab-owned application Ingresses and dependent objects, and keep the AWS Load Balancer Controller and EBS CSI controller running until their load balancers/volumes are confirmed removed. Never delete namespaces based only on names or remove finalizers blindly. Verify ALB/NLB/target groups, EBS/PVs, ENIs, NAT/EIP and RDS dependencies using direct service APIs.

Only after those dependencies are clear, create and inspect a saved destroy plan. The disposable RDS configuration skips a final snapshot and deletes automated backups; export any required database evidence/data before approving destruction.

```bash
set -euo pipefail
umask 077
: "${EXPECTED_ACCOUNT_ID:?Set the verified workload account ID}"
: "${TF_STATE_BUCKET:?Set the exact existing state bucket name}"
export AWS_REGION=eu-west-1 AWS_DEFAULT_REGION=eu-west-1
test "$(aws sts get-caller-identity --query Account --output text)" = "$EXPECTED_ACCOUNT_ID"
test "$AWS_REGION" = eu-west-1
terraform -chdir=infra/terraform/environments/lab init -reconfigure -input=false -backend-config="bucket=$TF_STATE_BUCKET"
test "$(terraform -chdir=infra/terraform/environments/lab workspace show)" = default
terraform -chdir=infra/terraform/environments/lab plan -destroy -out="$HOME/stage-1b-destroy.tfplan"
terraform -chdir=infra/terraform/environments/lab show "$HOME/stage-1b-destroy.tfplan"
```

Review the destroy plan and approve it separately. Only the human operator runs:

```bash
terraform -chdir=infra/terraform/environments/lab apply "$HOME/stage-1b-destroy.tfplan"
```

Then verify captured cluster, node-group, NAT/EIP, ENI, load-balancer and RDS IDs using successful AWS API responses. Investigate snapshots, retained backups, CloudWatch logs and Secrets Manager secrets explicitly. Preserve the Terraform backend and state key. If any required read fails, report **UNKNOWN** and stop; never infer clean teardown from an empty failed response. Review delayed billing separately and update `docs/SESSION_STATE.md` with observed results only.

The initial tiny fixture proof does not prove EKS teardown. No lifecycle script may be enabled until the complete dependent cleanup, partial-failure and cluster-unavailable recovery paths have been tested and separately approved.

The first approved proof is a tiny disposable-owned fixture; it does **not** prove EKS cleanup. Full EKS teardown must be demonstrated after infrastructure and platform installation. `docs/V3-SAFETY-CONTRACT.md` is authoritative.
