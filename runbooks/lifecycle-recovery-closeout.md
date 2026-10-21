# Lifecycle Batch 2 Recovery Closeout

This runbook resumes an interrupted recovery without changing the accepted
infrastructure, GitOps ownership, manual synchronization, or database approval
boundaries.

## Operator environment

Run from the reviewed recovery worktree. Preserve the operator's real home
directory so the existing AWS profile remains available; do not wrap recovery
with a temporary `HOME`. Set `AWS_PROFILE` or `LAB_AWS_PROFILE` explicitly to
the already approved profile.

Set `LAB_LIFECYCLE_EVIDENCE_DIR` to the authoritative private lifecycle
evidence directory. Worktrees may use a validated symlink to that directory;
the native checkpoint records its resolved path together with the AWS account,
region, EKS ARN, Terraform workspace, state lineage and serial, and exact Git
revision. An identity change invalidates completed checkpoints and forces live
revalidation. Conflicting or incomplete identity evidence fails closed.

The bootstrap classifies its behavior explicitly:

- `AUTO_REPAIR` is limited to local Terraform initialization from an approved
  mode-0600 backend file, isolated kubeconfig reconstruction, approved CLI
  discovery, and controller-owned localhost tunnel cleanup.
- `APPROVAL_REQUIRED` includes Kubernetes or Secret mutations, manual Argo CD
  synchronization, database restoration, and every Terraform apply or destroy.
- `FAIL_CLOSED` covers identity, ownership, backend, checkpoint, credential, or
  state ambiguity.

If Terraform is not initialized, point `LAB_TERRAFORM_BACKEND_CONFIG` at the
existing reviewed mode-0600 backend configuration. The controller never guesses
backend values. `KUBECONFIG` must name one isolated file and must not be the
operator's default kubeconfig. A stale isolated file is archived before an
identity-verified replacement is installed.

Use an isolated kubeconfig and verify it before recovery:

```bash
export KUBECONFIG="${XDG_RUNTIME_DIR:-/tmp}/eks-lab-recovery.kubeconfig"
umask 077
aws eks update-kubeconfig --name "$LAB_CLUSTER_NAME" \
  --region "$LAB_AWS_REGION" --alias eks-reliability-lab
test "$(kubectl config current-context)" = eks-reliability-lab
```

The native controller additionally compares the selected context's server and
certificate authority with the approved EKS identity and fails closed on any
mismatch.

Discover `argocd` from the existing `PATH`; do not download an unreviewed CLI
during recovery. Authenticate it through the already approved operator method,
then verify `argocd app get platform-root -o json` and the Kubernetes
Application identify the same UID before any manual synchronization. Never add
`--force` or `--prune`.

The accepted CLI may instead be selected with `LAB_ARGOCD_CLI`. The authenticated
Argo CD context, or `LAB_ARGOCD_SERVER`, must resolve to `localhost` or
`127.0.0.1`. The controller reuses a working authenticated tunnel, otherwise it
starts one bounded port-forward and cleans up only that child process.

## Resume boundary

Run `labctl native-platform-restore` from an interactive controlling terminal.
The controller revalidates checkpoints and prints progress before each stage.
Grafana username input and both non-echoing password prompts use `/dev/tty`;
credentials are sent to `kubectl create -f -` on standard input and are never
written to arguments, logs, checkpoints, Git, or evidence.

Stop after the six platform readiness stages for independent review. Database
restoration remains a separate `native-database-restore` operation requiring
the existing account-bound confirmation and Project Owner approval. Do not run
Terraform apply/destroy, force synchronization, pruning, CRD replacement, or a
database restore as part of platform closeout.

Failures report the stage, command binary, exit status or timeout while
suppressing command stderr. Preserve the private checkpoint and sanitized
evidence; investigate the named stage and retry only after actual state is
known.

## Rollback

Before publication, rollback is source-only: revert the hardening commit or
discard its isolated worktree. Do not delete the Grafana Secret or alter live
checkpoints to roll back code. If a live retry has already created the Secret,
stop for review and reconcile observed state through the normal readiness
check rather than overwriting or deleting it.

### Verified bootstrap and recovery invariants

- The native bootstrap first checks the approved AWS account and EKS identity.
  A missing local Terraform backend may be initialized only from an unambiguous
  initialized Git worktree's S3 bucket/key/region, with the tracked backend
  key/region cross-checked; it never creates or migrates remote state.
- `KUBECONFIG` may be absent. The bootstrap searches restricted historical
  private evidence for a matching EKS endpoint and CA, or creates a new
  isolated kubeconfig under private evidence. It never modifies `~/.kube/config`.
- The Argo CD CLI may be absent from `PATH`. Identical, working private CLI
  copies establish an executable identity; conflicting copies fail closed.
  The current authenticated localhost context and actual Kubernetes Service
  port are verified before opening a temporary, owned tunnel.
- Checkpoints retain full revision/serial audit metadata, but only changes to
  account, region, EKS ARN, Terraform workspace/lineage, or evidence root
  invalidate stage completion. Every completed stage still requires live
  readiness validation before it is skipped.
- Prometheus Operator StatefulSets are identified by the verified
  `release=observability` and `managed-by=prometheus-operator` labels, then
  exact names, UID, namespace, and readiness; Helm Deployments are identified
  by `instance=observability` plus exact names. No Kubernetes relabeling.
- Command failures report an allowlisted error category and exit code, never
  raw stderr, which may contain provider or secret values.
- Source-level tests do not replace native WSL validation or live recovery
  acceptance. Database restoration and destructive operations require their
  separate Project Owner approvals.
