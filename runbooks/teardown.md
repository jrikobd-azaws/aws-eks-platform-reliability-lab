# Teardown runbook — design only; stop.sh intentionally blocked

**As of the consolidated starter, there is no tested automated EKS teardown.** Never treat `scripts/stop.sh` as a working cleanup script, and never rely on tag-only discovery as proof that no billable resources remain.

When implemented after approved tests, the human-operated workflow must:
1. Verify exact STS account, Region, EKS cluster ARN/endpoint, Terraform backend/workspace, Project+Stack tags and captured resource IDs.
2. Export raw operational evidence privately and safely suspend GitOps reconciliation.
3. Remove only confirmed lab-owned application Ingress and dependent objects; keep ALB/CSI controllers available until dependent AWS load balancers/volumes have been cleaned up.
4. Confirm actual AWS ALB/NLB, target groups, ENIs, EBS/PVs, NAT/EIPs and RDS dependency behaviour via direct service APIs, not tag API alone.
5. Review and separately approve a saved Terraform destroy plan for the disposable stack; never blindly delete state locks or Kubernetes finalizers.
6. Confirm expected exact owned resources are absent using successful AWS queries, distinguish permanent resources and inspect snapshots/retained backups, logs and secrets scheduled for deletion.
7. If a read fails, return **UNKNOWN** and investigate; do not classify it as clean. Have a separately approved fallback for a non-responsive Kubernetes API/partially created cluster.
8. Review cost and billing later (data can lag), record observed state in `docs/SESSION_STATE.md` and sanitise evidence before publishing.

The first approved proof is a tiny disposable-owned fixture; it does **not** prove EKS cleanup. Full EKS teardown must be demonstrated after infrastructure and platform installation. `docs/V3-SAFETY-CONTRACT.md` is authoritative.
