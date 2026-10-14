# Implementation roadmap — consolidated v3 baseline

This roadmap preserves the 43 scenario IDs, six wave order and the original three public milestone points. **Required, stage-specific quality gates are defined in [`TESTING-STRATEGY.md`](TESTING-STRATEGY.md), including SonarCloud, Trivy, unit/integration tests, GitOps smoke tests, blocked-failure evidence and QA promotion tests.** Stages are dependency ordered so bootstrap can proceed without requiring a functioning EKS application or validated teardown before a cluster exists. Time ranges are **unverified planning guesses**, not delivery promises.

| Stage | Implementation scope (manual first, automation second) | Exit proof |
|---|---|---|
| 0 — Local safety | Verify consolidated baseline, toolchain, contract, scenarios and blocked launchers; plan minimal Python preflight; mock unsafe cases when implemented | Required tools and checks pass locally. All four launchers remain blocked (78) |
| 1A — Governance & permanent foundation | Confirm Region/account/credits/quotas/budget/tagging; Control Tower, S3 state, GitHub OIDC, remote plan workflow, ECR | Plan-only identity works, a saved plan is reviewed and every apply is individually approved |
| 1A-F — Tiny disposable fixture | Create a small owned resource with approved Terraform plan; test wrong account, AccessDenied/timeout, exact inventory and verified destroy | Fixture deletion confirmed by service APIs; failed reads are UNKNOWN, not clean |
| 1B — Infrastructure | Manual Terraform VPC, two-AZ private/public networking, single NAT, EKS 1.36/nodes, IRSA, ECR, RDS | Infra-layer validation; real outputs recorded |
| 1B-P — Platform bootstrap | Pin and manually install required EKS add-ons/controllers, Argo CD and secret integration; then reproduce using focused Python scripts | Component health, cluster identity, no hidden partial failures |
| 1B-R — Rebuild safety | Validate owned resource inventory, ALB/EBS/ENI/RDS teardown ordering, partial-failure fallback, full destroy/recreate | Actual end-to-end teardown and rebuild proven; no false CLEAN on failed API responses |
| 1C — Application delivery | Pin upstream app; frontend/FastAPI unit tests and DB integration; SonarCloud quality, Trivy scans and blocked CI; build two images, ECR; raw manifests in sandbox; Helm; Argo CD DEV | Required source, dependency, container and manifest gates pass; ALB→frontend→backend→RDS transaction and GitOps health actually verified |
| 1D — Reliability & promotion | Minimal Python validators/evidence export and alert tests; **then**, after approval, QA→PROD-like GitOps promotion and regression/smoke checks | Monitoring signals tested; QA release gates actually block a failing change; approved immutable-digest promotion and rollback verified |
| Wave 1 | K8S-01 through K8S-10 | 10 evidence-backed scenarios. **PUBLISH POINT 1** |
| Wave 2 | K8S-11 through K8S-13, EKS-01, EKS-02 | 5 evidence-backed scenarios |
| Wave 3 | HELM-01 through HELM-04, ARGO-01 through ARGO-06 | 10 evidence-backed scenarios. **PUBLISH POINT 2** |
| Wave 4 | TF-01 through TF-09 (isolated sandbox for state/destructive work) | 9 evidence-backed scenarios |
| Wave 5 | INC-01 through INC-06 | 6 cross-layer incidents. **PUBLISH POINT 3** |
| Wave 6 | EKS-03, EKS-04, EKS-05 | 3 scenarios; **43/43** evidence-backed scenarios total |
| Later | AI/RAG diagnostic assistant | Only after a verified incident/runbook corpus exists; not a core blocker |

**Execution model:** Within every build stage, perform reviewed manual actions, inspect real results, automate only the proven steps and demonstrate repeatability. All external writes require explicit human approval, saved Terraform plan review and exact-account/resource targeting.

**Publication:** K8S-01 is the first full demonstration, **not** Publication Point 1. The first planned publication milestone remains K8S-01–10 complete. A smaller preview may be shared only if clearly marked as partial and approved by the owner.

Read `docs/IMPLEMENTATION-GUIDE.md` for the execution sequence and `docs/TRAINING-MAP.md` for the Udemy course mapping.
