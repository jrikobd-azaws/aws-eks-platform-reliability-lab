# Stage 2 platform bootstrap

This runbook is for the human operator. The agent did not call AWS, kubectl, Helm, Argo CD, or mutate Terraform state. The EKS cluster and add-ons are reused; this stage adds platform IAM to the existing lab Terraform root and then installs Argo CD, AWS Load Balancer Controller, External Secrets Operator, namespace controls and the gp3 StorageClass.

## Dependencies and pinned charts

| Component                          |                               Version | Dependency                                                    |
| ---------------------------------- | ------------------------------------: | ------------------------------------------------------------- |
| Argo CD Helm chart                 |                  10.10.3 (app v3.5.5) | Existing EKS 1.36 control plane and nodes                     |
| AWS Load Balancer Controller chart |                                 3.6.0 | Existing cluster OIDC provider, IAM role, VPC and subnets     |
| External Secrets chart             |                                2.12.0 | Existing cluster OIDC provider and dev secret-reader IAM role |
| gp3 StorageClass                   | EBS CSI provisioner `ebs.csi.aws.com` | Existing EBS CSI add-on                                       |
| RDS ExternalSecret                 |     ESO `external-secrets.io/v1` CRDs | ESO ready; exact Terraform RDS secret ARN supplied            |

Argo CD uses one controller/server/repo-server/Redis replica, keeps its UI on ClusterIP, disables Dex, notifications, ApplicationSet, commit server and Redis HA. There is no Argo LoadBalancer or ingress. Use port-forward for initial access. LBC is two small replicas; WAF and Shield integrations are disabled, and no ALB is created until a workload Ingress is applied. ESO is one replica with small requests/limits. The exact chart versions and IAM policy source are fixed in code; review upstream release notes before a future version change.
The pinned Argo chart declares Kubernetes `>=1.25.0-0` and ESO chart `>=1.19.0-0`. The pinned LBC chart has no `kubeVersion` constraint in its Chart metadata, so verify its v3.6.0 release notes against the running EKS 1.36 cluster before install; Gateway API is not configured by this lab.

## Preflight and local render

Run as the human in the approved execution shell. Do not expose the administrator profile to the agent. The checks below verify the exact account, cluster ARN, endpoint and CA before any cluster write.

```bash
set -euo pipefail
umask 077
export AWS_PROFILE='<approved-human-profile>'
export AWS_REGION=eu-west-1 AWS_DEFAULT_REGION=eu-west-1 AWS_EC2_METADATA_DISABLED=true
export EXPECTED_ACCOUNT_ID='<verified-12-digit-account-id>'
TF_DIR=infra/terraform/environments/lab
export TF_STATE_BUCKET='<exact-existing-permanent-state-bucket>'
export TF_VAR_expected_account_id="$EXPECTED_ACCOUNT_ID"
export TF_VAR_cluster_admin_principal_arn='arn:aws:iam::<account-id>:user/<existing-human-admin-user>'
export TF_VAR_cluster_endpoint_public_access_cidrs='["<verified-public-ip>/32"]'
export TF_VAR_db_engine_version='16.15'
terraform -chdir="$TF_DIR" init -reconfigure -input=false \
	-backend-config="bucket=$TF_STATE_BUCKET" \
	-backend-config="key=lab/terraform.tfstate" \
	-backend-config="region=eu-west-1" \
	-backend-config="encrypt=true" \
	-backend-config="use_lockfile=true"
test "$(terraform -chdir="$TF_DIR" workspace show)" = default
test "$(aws sts get-caller-identity --query Account --output text)" = "$EXPECTED_ACCOUNT_ID"
CLUSTER_NAME="$(terraform -chdir="$TF_DIR" output -raw cluster_name)"
EXPECTED_CLUSTER_ARN="$(terraform -chdir="$TF_DIR" output -raw cluster_arn)"
CLUSTER_JSON="$(aws eks describe-cluster --region "$AWS_REGION" --name "$CLUSTER_NAME" --output json)"
test "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["cluster"]["arn"])' <<<"$CLUSTER_JSON")" = "$EXPECTED_CLUSTER_ARN"
test "$(kubectl config view --raw --minify -o jsonpath='{.clusters[0].cluster.server}')" = "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["cluster"]["endpoint"])' <<<"$CLUSTER_JSON")"
test "$(kubectl config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')" = "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["cluster"]["certificateAuthority"]["data"])' <<<"$CLUSTER_JSON")"
test "$(kubectl get nodes --no-headers | awk '$2 == "Ready" { count++ } END { print count+0 }')" -eq 2
for addon in vpc-cni coredns kube-proxy aws-ebs-csi-driver; do
	test "$(aws eks describe-addon --region "$AWS_REGION" --cluster-name "$CLUSTER_NAME" --addon-name "$addon" --query addon.status --output text)" = ACTIVE
done
```

The Stage 2 Terraform source adds only two IRSA roles, their policies and attachments to the existing disposable lab state. Review this saved plan for those IAM changes only; stop if it replaces or changes the EKS cluster, VPC, nodes, RDS or permanent foundation. The human operator approves and applies the reviewed plan:

```bash
terraform -chdir="$TF_DIR" init -input=false
terraform -chdir="$TF_DIR" validate
terraform -chdir="$TF_DIR" plan -out="$HOME/stage-2-platform-iam.tfplan"
terraform -chdir="$TF_DIR" show "$HOME/stage-2-platform-iam.tfplan"
```

After independent human review, apply that exact saved plan:

```bash
terraform -chdir="$TF_DIR" apply "$HOME/stage-2-platform-iam.tfplan"
```

Get the non-secret identifiers from the existing Terraform state and verify they are non-empty:

```bash
LBC_ROLE_ARN="$(terraform -chdir="$TF_DIR" output -raw aws_load_balancer_controller_role_arn)"
DEV_SECRET_ROLE_ARN="$(terraform -chdir="$TF_DIR" output -raw dev_secrets_reader_role_arn)"
RDS_MASTER_SECRET_ARN="$(terraform -chdir="$TF_DIR" output -raw rds_master_secret_arn)"
VPC_ID="$(terraform -chdir="$TF_DIR" output -raw vpc_id)"
test -n "$LBC_ROLE_ARN" && test -n "$DEV_SECRET_ROLE_ARN" && test -n "$RDS_MASTER_SECRET_ARN" && test -n "$VPC_ID"
```

The role and secret ARNs are identifiers, not credentials; never retrieve or print secret values. Before Argo sync, replace the explicit `REPLACE_WITH_TERRAFORM_OUTPUT_*` markers in `gitops/platform/values/aws-lbc-values.yaml` and `gitops/platform/dev-secrets/` with the exact outputs above. Also set the actual cluster name in the LBC values. Do not commit account-specific identifiers to a public repository. If this repository is public, stop before sync until an approved private configuration source is available; do not weaken IAM or broaden secret access to avoid that gate.

Check rendered charts locally before installing:

```bash
helm repo add argo https://argoproj.github.io/argo-helm
helm repo add eks https://aws.github.io/eks-charts
helm repo add external-secrets https://charts.external-secrets.io
helm repo update
helm show chart argo/argo-cd --version 10.10.3
helm show chart eks/aws-load-balancer-controller --version 3.6.0
helm show chart external-secrets/external-secrets --version 2.12.0
helm template argocd argo/argo-cd --namespace argocd --version 10.10.3 -f platform/bootstrap/argocd-values.yaml > "$HOME/argocd-rendered.yaml"
helm template aws-load-balancer-controller eks/aws-load-balancer-controller --namespace kube-system --version 3.6.0 -f gitops/platform/values/aws-lbc-values.yaml > "$HOME/aws-lbc-rendered.yaml"
helm template external-secrets external-secrets/external-secrets --namespace platform-system --version 2.12.0 -f gitops/platform/values/external-secrets-values.yaml > "$HOME/external-secrets-rendered.yaml"
kubectl apply --server-side --dry-run=server -f platform/bootstrap/storageclass-gp3.yaml
```

Review all three render files and the local Kubernetes YAML check before the next step. The bootstrap StorageClass file is a server-side dry-run preview; the matching `gitops/platform/storageclass-gp3.yaml` is the sole GitOps-managed object. If Helm or a schema validator is unavailable, that chart validation is **not passed**; install the reviewed local tool or defer cluster changes. Do not treat YAML syntax validation as API/schema validation.

## Manual learning, then Argo handoff

First create only the named platform namespaces and their Pod Security labels, then install Argo CD manually with the pinned chart. The UI remains cluster-internal:

```bash
kubectl apply -f gitops/platform/namespaces.yaml
helm upgrade --install argocd argo/argo-cd --namespace argocd --version 10.10.3 --values platform/bootstrap/argocd-values.yaml --wait --timeout 15m
kubectl -n argocd rollout status deployment/argocd-server --timeout=5m
kubectl -n argocd port-forward svc/argocd-server 8080:443
```

In a second terminal, install the controllers manually once to inspect their release, events, probes and resource use. These installs create no ALB without a workload Ingress. The values file must already contain the exact output identifiers.

```bash
helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller --namespace kube-system --version 3.6.0 --values gitops/platform/values/aws-lbc-values.yaml --set "clusterName=$CLUSTER_NAME" --set "vpcId=$VPC_ID" --set-string "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn=$LBC_ROLE_ARN" --wait --timeout 10m
helm upgrade --install external-secrets external-secrets/external-secrets --namespace platform-system --create-namespace --version 2.12.0 --values gitops/platform/values/external-secrets-values.yaml --wait --timeout 10m
kubectl -n kube-system rollout status deployment/aws-load-balancer-controller --timeout=5m
kubectl -n platform-system rollout status deployment/external-secrets --timeout=5m
kubectl get pods -n kube-system -o wide
kubectl get pods -n platform-system -o wide
```

These manual Helm releases are a learning pass, not a second long-lived installation. After observing them and confirming no Ingress, TargetGroupBinding, SecretStore, ExternalSecret or workload depends on them, uninstall those two releases before handing ownership to Argo. Keep the EBS CSI add-on installed. Then let Argo CD create the single GitOps-managed release of each controller; never leave the manual Helm release and Argo release installed together.

```bash
helm uninstall aws-load-balancer-controller --namespace kube-system
helm uninstall external-secrets --namespace platform-system
kubectl apply -k gitops/argocd
```

The root Application tracks `main` with automatic synchronization disabled. An
explicit manual root sync creates the baseline namespace/StorageClass/RBAC
resources and manual-sync child Applications without pruning. Merge the
reviewed source to `main` before applying it. After confirming the
Terraform-output markers have been replaced in the reviewed repository
revision, sync the two pinned controller applications:

```bash
argocd app sync aws-load-balancer-controller
argocd app sync external-secrets
argocd app wait aws-load-balancer-controller --sync --health --timeout 600
argocd app wait external-secrets --sync --health --timeout 600
```

Only after ESO is Healthy and its CRDs are established, sync the dev secret resources:

```bash
argocd app sync dev-secrets
argocd app wait dev-secrets --sync --health --timeout 300
```

The dev SecretStore uses IRSA through the dedicated `dev/external-secrets-reader` service account. Terraform restricts that role to `DescribeSecret` and `GetSecretValue` on the exact RDS-managed secret ARN. The target Kubernetes Secret contains credentials at runtime; never inspect it with `kubectl get secret -o yaml`, log its values, or commit its content. The `lab-developers` RoleBinding grants namespace-only read access and no Secret access; verify the human's EKS identity is mapped to that Kubernetes group before relying on it.

## Validation and evidence

The post-bootstrap validator is read-only. It verifies STS account, exact cluster ARN, kubeconfig endpoint/CA, two Ready nodes, required namespaces, gp3 configuration, controller rollouts, Argo Application health and ExternalSecret readiness without printing secret values:

```bash
EXPECTED_ACCOUNT_ID='<verified-12-digit-account-id>' AWS_REGION=eu-west-1 scripts/validate-platform.sh
```

Keep raw command output and resource IDs private in `.private-evidence/`; record sanitized evidence and actual results in `docs/SESSION_STATE.md`. A missing object, failed read, timeout, or access denial is UNKNOWN/FAIL, not healthy or absent. Do not claim workload-to-RDS success until the later application stage verifies a real transaction.

## Rollback

- Before Argo handoff, inspect `helm history` and use `helm rollback <release> <revision> --namespace <namespace>` only after reviewing the exact revision and impact.
- After Argo owns a resource, revert the bad Git commit and let Argo reconcile; do not use `argocd app rollback` as a durable GitOps fix.
- If LBC is unhealthy, do not delete its AWS load balancers or TargetGroupBindings blindly. Inspect controller logs/events and AWS resources; keep the controller available while owned Ingress resources are removed.
- If ESO is unhealthy, do not delete synced Secrets or the RDS-managed source secret to clear an error. Correct the exact IRSA trust, secret ARN or provider configuration, then resync.
- Keep the root app's automated pruning disabled. Any deletion must be reviewed as a named resource change.

## Safe teardown

1. Export evidence privately and verify exact STS account, Region, EKS ARN, endpoint/CA, Terraform backend/workspace and resource IDs.
2. Stop new workloads and remove only named dev Ingresses/PVCs through reviewed Git changes. Keep LBC and EBS CSI running until their AWS load balancers, target groups and EBS volumes are confirmed gone.
3. Confirm no Ingress, TargetGroupBinding, PVC, SecretStore, ExternalSecret or workload depends on the corresponding controller. Read AWS APIs successfully; an API failure is UNKNOWN.
4. Remove the dev ExternalSecret/SecretStore and verify the generated Kubernetes Secret is gone without printing its data. Keep the RDS source secret until the database teardown plan is approved.
5. Delete the named Argo child Applications only after reviewing their cascade behavior. Remove LBC and ESO after their managed resources are clear. Remove Argo CD last. Never delete `kube-system`, and never delete a namespace solely by name.
6. Review a saved Terraform destroy plan for the disposable `lab` root separately. The IAM roles and policies added for this platform are in that root and are destroyed only with human approval. Preserve the permanent state backend, budget, GitHub OIDC role/provider and state.
7. Verify captured EKS, NAT/EIP, ENI, load balancer, target group, EBS, RDS, Secrets Manager and IAM resource IDs absent using successful service API reads. Update session state with observed facts only.

For the eventual application cleanup, remove its Ingress and PVC resources before removing the controllers or StorageClass. The full EKS lifecycle scripts remain blocked until these steps, partial-failure recovery and cluster-unavailable cleanup have been tested and separately approved.
## Approved public GitOps identifiers

For this disposable, single-account portfolio lab, the operator has
approved publishing only the following non-secret identifiers:

- EKS cluster name
- VPC ID
- AWS Load Balancer Controller IAM role ARN
- Dev secrets-reader IAM role ARN
- RDS Secrets Manager secret ARN

This exception applies only to the approved lab GitOps configuration.
It does not authorise publishing credentials, secret values, access
keys, tokens, Terraform state, or private configuration.

IAM permissions, IRSA trust restrictions, protected GitHub workflows,
manual deployment approvals and the V3 safety contract remain in force.

The publication of these identifiers exposes infrastructure metadata
and is not a general production security recommendation.

## Stage 1D-C observability recovery

The authoritative Argo CD bootstrap values are maintained in
`platform/bootstrap/argocd-values.yaml`.

The Argo CD Application Controller must be restored with:

- One replica.
- CPU request `100m`, CPU limit `500m`.
- Memory request `512Mi`, memory limit `1Gi`.

The recovery process must discover the existing Helm release
revision and status rather than assume a previous live revision.

### Required recovery sequence

1. Restore EKS and Argo CD using the established bootstrap procedure.
2. Verify controller resources and readiness.
3. Restore the required AppProject permissions.
4. Reconcile `observability-crds` and establish all ten CRDs.
5. Restore the Grafana administrator Secret securely.
6. Manually synchronize the three-source `observability` Application.
7. Verify Prometheus, Alertmanager, Grafana and kube-state-metrics.
8. Verify FastAPI `/metrics` target UP.
9. Verify all three baseline Prometheus alert rules.
10. Verify the FastAPI Grafana dashboard and datasource query.
11. Save sanitized acceptance evidence and resource consumption.

Preserve manual root synchronization, the Stage 3 pinned application
revision, immutable application images and existing RDS/Alembic
recovery requirements.

Monitoring storage remains ephemeral with short retention.
Grafana remains private through ClusterIP and port-forward.

CloudWatch assessment: one relevant log group, no justified
retention changes, and no additional CloudWatch agents required.

Lifecycle Batch 2 certification must verify that the controller
resource settings are reproducible after cluster recreation.

### Lifecycle Batch 2 recovery controller

`scripts/lifecycle/recover_platform.py` preserves the existing
`labctl native-platform-restore` entry point and records an atomic private
checkpoint after each independently revalidated stage. A retry first reads
actual Helm, Argo CD and Kubernetes state; API errors fail closed and do not
authorize a write merely because a checkpoint is incomplete.

The controller restores the Argo CD AppProjects before their Applications,
uses explicit manual `argocd app sync` operations, establishes the approved
ten Prometheus Operator CRDs before the monitoring workload, and creates the
Grafana administrator Secret only from an interactive `/dev/tty` prompt. The
Secret payload is passed on standard input and is not written to arguments,
logs, checkpoints or evidence.

Database restore remains the existing separately confirmed
`native-database-restore` operation. Execute-mode recovery then validates the
exact RDS identity, Alembic head `fe56fa70289e`, required UUID tables,
`app_runtime` DML access without schema `CREATE`, immutable Stage 3 images and
FastAPI live, ready and metrics endpoints. A partial or non-empty database is
not automatically dropped, downgraded or restored.

### Backup-bound shutdown certification

`native-restore-test` requires an account- and backup-hash-bound confirmation
before creating a uniquely named `restore_test_*` database. It restores the
current approved encrypted backup without targeting the live `app` database,
verifies Alembic `fe56fa70289e`, UUID relations and restricted `app_runtime`
DML, then proves the disposable database, validation pod and credential Secret
are absent. Any restore, validation or cleanup uncertainty records failed
private evidence and blocks shutdown certification.

After reviewing the exact native shutdown plan, `native-shutdown-certify`
requires a confirmation bound to that plan's SHA-256. It rechecks Terraform
state lineage and serial, protected resources, load-balancer and storage
dependencies, Argo CD destination identity and manual synchronization. Only
then does it atomically create `.private-evidence/lifecycle/shutdown-readiness.json`.
The record expires after 24 hours and is still not permission to execute the
saved destroy plan.

### Final-backup freshness requirement

Shutdown certification requires an encrypted backup created within the approved 24-hour window and a successful restore test performed after that exact backup. Application writes after the final backup invalidate the recovery-point assumption: create a new encrypted backup and complete a new restore test before destruction.
