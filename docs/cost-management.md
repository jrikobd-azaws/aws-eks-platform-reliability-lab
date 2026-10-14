# Cost management — planned controls, not deployed proof

The original v2 planning envelope is about USD 160 across both projects and an **aspirational** <= USD 100 for this lab. These are user planning targets, **not current measured costs** or guarantees. Verify Region-specific AWS prices, Control Tower costs, quotas, credits, RDS class, node sizing and budget assumptions before cloud provisioning.

## Intended controls
- AWS Budgets alerts at the locked 50%, 75%, 90% and 100% levels (not yet verified deployed).
- Keep expensive per-hour resources in the ephemeral lab stack; permanent resources (Control Tower, AWS Config/CloudTrail, S3, ECR, CloudWatch, snapshots and data transfer) can still incur charges.
- Apply exact Project and Stack ownership tagging; AWS Tagging API is **not** a complete inventory or deletion proof.
- Review AWS Billing/Cost Explorer after sessions (charges can lag); record measured spend, not invented savings.

## Chargeable resources to inventory explicitly
EKS control plane, EC2 nodes, NAT gateways, idle EIPs, ALB/NLB and target groups, EBS/PVs, RDS and retained snapshots/backups, ENIs, CloudWatch logs/metrics, ECR storage, AWS Config/CloudTrail, secret stores. Investigate untagged or orphaned resources manually; never automatically delete unrelated resources.

## Evidence and session records
Raw run data and exact resource inventory belong under `.private-evidence/` and `.runtime-inventory/` (gitignored), then sanitised material can be committed to `docs/evidence/`. Session start/stop times, destroy verification and real billing measurements are **unverified until implemented**. The v3 launchers are blocked at this stage.
