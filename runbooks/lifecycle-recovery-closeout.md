# Lifecycle Batch 2 Recovery Closeout

This runbook resumes an interrupted recovery without changing the accepted
infrastructure, GitOps ownership, manual synchronization, or database approval
boundaries.

## Operator environment

Run from the reviewed recovery worktree. Preserve the operator's real home
directory so the existing AWS profile remains available; do not wrap recovery
with a temporary `HOME`. Set `AWS_PROFILE` or `LAB_AWS_PROFILE` explicitly to
the already approved profile.

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
