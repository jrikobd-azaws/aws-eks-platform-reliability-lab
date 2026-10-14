# Implementation procedure: manual → verify → automate → rebuild → troubleshoot

**Read** the v3 contract, `DECISIONS.md`, `SESSION_STATE.md` and `ROADMAP.md` before any action. This is a dependency-ordered guide, **not** permission to run external writes. Every cloud apply/deploy/cleanup requires human review and authorisation. Copy no ZenPharma credentials, account IDs or default-yes prompts.

**Mandatory cross-stage quality gates:** `docs/TESTING-STRATEGY.md` maps the ZenPharma workshop's testing/scanning and the lab-specific FastAPI/Python/database tests to the exact stage when they are needed. Apply a stage's checks before declaring that stage complete; do not build all test infrastructure in Stage 0.

## Stage 0 — prepare the local repository (no cloud writes)
1. Extract one consolidated ZIP; use WSL/Bash and VS Code at repository root.
2. Check source ownership; run `check-v3-patch.py`, scenario checker and required local tooling checks.
3. Review missing decisions/unsafe old scripts; lifecycle launchers intentionally exit 78.
4. Propose a minimal testable Python `scripts/labops/` with config, preflight, runner, reporting **only when implementation of corresponding operations begins**. The preflight must confirm exact account/Region/cluster/backend/repo identity; previews are read-only and default-on; a bare Enter never approves.
5. Mock failed reads, wrong targets, timeout and zero resources; no unsupported success claims. Record real checks.

## Stage 1A — governance and permanent foundation
1. Confirm Region, AWS account(s), Control Tower prerequisites, credit eligibility, IAM Identity Center access, approved roles, quota, instance availability and estimated spend from current prices.
2. Design public/private tagging and exact Project + Stack ownership. Separate isolated AI plan-only credentials and human execution credentials.
3. Bootstrap S3-backed Terraform state securely; enable versioning/encryption/locking and verify the intended backend/workspace. Design GitHub OIDC role trust conditions and budget alarms.
4. Use **local manual Terraform** fmt/validate/plan, capture a saved plan, inspect and obtain authorisation for each apply. Then implement GitHub Actions PR validation/plan. Never auto-apply infrastructure on push.
5. Test a tiny Terraform-managed owned disposable resource: expected vs wrong AWS account, failed API results, inventory, deletion and post-deletion AWS-side confirmation. Do not treat this as evidence of full EKS teardown.

## Stage 1B — build AWS infrastructure using Terraform
1. Manually author and validate VPC, public/private subnets across 2 AZs, routes, one NAT, security groups and endpoint design. Choose a non-overlapping CIDR.
2. Deploy EKS 1.36 **only if confirmed available** in the chosen Region. Start with two managed nodes of the documented t3.medium target; measure headroom before guaranteeing capacity or deploying monitoring.
3. Add ECR repos, IAM/IRSA and a private single-AZ PostgreSQL RDS instance. Keep credentials out of Git, state, CLI arguments and logs where practicable; select approved secret delivery before application deployment.
4. Validate service inventories, node status, IAM and database security. Keep named account/cluster verification and ownership checks.

## Stage 1B-P — manually install, then script platform add-ons
1. Manually install approved pinned AWS Load Balancer Controller, EBS CSI if needed, Argo CD and secrets integration. Observe `helm template`, installation, rollout, logs/events and AWS controller effects.
2. Once each manual operation is understood and proven, create a **small** Python bootstrap CLI for it, using common `labops` preflight, explicit per-action approval, default dry-run, timeouts and structured results.
3. Compare Python-driven results with manual baseline. Failure to find an expected component is a failure, never a successful empty query.

## Stage 1B-R — prove safe session lifecycle
1. Build stage-aware infrastructure/platform/application validators; confirm full app validation is not required for infra-only stages.
2. Test cleanup dependency ordering: stop GitOps reconciliation safely, remove owned workload Ingress, watch AWS ALB/NLB/target groups clear while controllers remain available, handle PVC/EBS/CSI and RDS dependencies, then approved Terraform destroy.
3. Inventory ENIs, NAT/EIPs, EBS, ALBs, RDS/snapshots, logs and secrets using service APIs and captured resource IDs. Check for unrelated resources **without automatically deleting them**.
4. Prove recovery if provisioning partially fails or Kubernetes API is unavailable; fail closed if API inventory is UNKNOWN. **Only after observed tests** may launchers be enabled under an explicit new approved task.

## Stage 1C — deploy application DEV (ZenPharma-style)
1. Pin the FastAPI upstream app commit; implement only allowed frontend/backend changes and seed data.
2. Implement frontend lint/unit tests, FastAPI `pytest`/HTTPX tests, isolated PostgreSQL integration tests and SonarCloud/code-quality gate (verify private-repo eligibility), then build and test **two** Docker images. Run Trivy source/dependency and image scans; publish via ECR with GitHub Actions OIDC using immutable digests **only after mandatory release gates pass**. Capture both a negative/failing test and a successful rerun.
3. Learn raw Kubernetes Deployment/Service/ConfigMap/Ingress/probes using a short-lived sandbox, then use the Helm chart for the real app.
4. Register GitOps repository and app-of-apps; Argo CD reconciles the reviewed DEV Helm source. Use a **separate** Helm-native sandbox for Helm history/rollback incidents.
5. Prove ALB → frontend → backend → private RDS with an actual read/write transaction and observe pod readiness, application logs, Argo health and ALB target health.

## Stage 1D — monitoring, Python evidence and optional promotion
1. Set up trimmed Prometheus/Grafana and approved CloudWatch signals. Measure node/cluster overhead, retain sanitised dashboards and logs.
2. Implement Python read-only platform verification and private evidence export, with JSON report outcomes for simulated, executed, failed and unknown steps.
3. **Only after DEV and teardown are proven and the owner approves an explicit decision**, add QA and PROD-like namespaces, Argo CD apps, values and separated DB credentials on the same temporary cluster.
4. Demonstrate immutable image-digest DEV→QA→PROD-like promotion through reviewed Git PRs, **blocking** QA smoke/regression tests and manual PROD-like sync; verify digest equality, user-facing smoke checks and a Git revert rollback. Capture both blocked and successful promotion evidence. Do not advertise these as three independently isolated production clusters.

## Troubleshooting — all 43 scenarios in six waves
- Wave 1: ten Kubernetes fundamentals, starting with K8S-01 as fully verified reference. Use the Udemy course as supplemental local break/fix training (see `TRAINING-MAP.md`).
- Wave 2: K8S-11..13 and EKS-01/02. Verify actual EKS-specific behaviour.
- Wave 3: HELM-01..04 and ARGO-01..06. Keep native Helm sandbox distinct from Argo-managed state.
- Wave 4: TF-01..09 with dedicated sandbox backend/roles for state and failed-apply exercises.
- Wave 5: INC-01..06 crossing GitHub/IAM/AWS/EKS/Helm/Argo/app/RDS layers.
- Wave 6: EKS-03..05 including the supported and available 1.36→1.37 upgrade exercise.

For **every** incident capture baseline, explicit scope and abort threshold, injected fault, symptom, observations, hypothesis, measured root cause, smallest recovery, technical/user recovery and prevention. Store raw evidence privately and publish only sanitised excerpts. Preserve the original publication gates after Waves 1, 3 and 5. Maintain portfolio claims about **completed** work only.

## Non-negotiable checkpoint before ending any cloud session
Record actual account/region and owned resource IDs privately, take private evidence exports, obtain approval for cleanup, verify both targeted and account-side inventory APIs, review billing separately and update `SESSION_STATE.md` truthfully. Never report CLEAN when any required read failed.
