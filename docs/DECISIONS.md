# Decisions

## A. Locked decisions (change only by the human editing this file)
| # | Topic | Decision |
|---|---|---|
| 1 | Governance | Control Tower landing zone + IAM Identity Center, left running |
| 2 | DR Region | Documented only (ADR-004) |
| 3 | Accounts | One email with plus-aliases for Log Archive/Audit (verify delivery first) |
| 4 | Kubernetes | Start 1.36, upgrade to 1.37 in EKS-05; re-verify availability and support dates on the EKS console immediately before deploying |
| 5 | Nodes | Managed node group, 2 x t3.medium (x86), 2 AZs |
| 6 | Database | RDS PostgreSQL single-AZ, small instance class (confirm availability), destroyed nightly, reseeded by script |
| 7 | Network | One VPC, public+private subnets in 2 AZs, single NAT gateway (documented trade-off) |
| 8 | Workload identity | IRSA core; Pod Identity in EKS-01 |
| 9 | Images | Two (frontend, backend), FastAPI template |
| 10 | Registry | ECR, two repos, keep last 10 images |
| 11 | Ingress | AWS Load Balancer Controller + ALB |
| 12 | GitOps | Argo CD app-of-apps + Helm chart in app/helm |
| 13 | Observability | kube-prometheus-stack (trimmed) + CloudWatch; export dashboards each session |
| 14 | Budget | About USD 160 across both projects; target <= USD 100 for this lab; alarms 50/75/90/100% |
| 15 | Session | About 3.5 h/day; create at start, destroy at end |
| 16 | Portfolio | MkDocs site azawslab.co.uk gets a new tab |
| 17 | State | S3 remote state, encrypted, locked, in the permanent stack |
| 18 | CI | GitHub Actions + AWS OIDC; plan on PR; apply via protected environment |
| 19 | Scenarios | All 43 in scope, six waves, no tiers; depth marker full/compact |
| 20 | AI controls | Plan -> approve -> implement -> validate -> record; agents use a plan-only profile; humans run apply and lifecycle scripts (ADR-005) |


## B. Blocking gates by the stage they protect

The original v2 gate list is preserved in substance, but the timing is corrected: Stage 0 cannot prove a full EKS teardown before EKS is provisioned. Every gate remains required **before the action it protects**, not before all infrastructure work. This is a consolidation proposal for human review; it does not approve cloud writes.

| Gate | Required before | Status | Resolved value |
|---|---|---|---|
| v3 safety baseline, required local tools and blocked launchers verified | Completion of Stage 0 | OPEN | Verify on user's workstation |
| Home Region chosen and account available | First cloud provisioning | OPEN | TBD |
| EXPECTED_ACCOUNT_ID and plan-only profile verified | First cloud provisioning | OPEN | TBD |
| Account plan, credits and Control Tower prerequisites checked | First cloud provisioning | OPEN | TBD |
| AWS budgets, resource tagging, quota and instance availability evaluated | First substantial cloud provisioning | OPEN | TBD |
| State backend, workspace and human approval of saved plan | First Terraform apply | OPEN | TBD |
| Tiny owned disposable-resource guardrail proof | EKS provisioning | OPEN | TBD |
| EKS 1.36 / 1.37 Region, support and add-on compatibility | EKS provisioning / upgrade respectively | OPEN | TBD |
| ARGOCD_VERSION pinned and install method verified | Platform add-on installation | OPEN | TBD |
| RDS class confirmed in Region | RDS provisioning | OPEN | TBD |
| VPC CIDR non-overlapping with 10.20, 10.100–120, 10.200–220 | VPC provisioning | OPEN | TBD |
| Secrets integration approved (e.g. Secrets Manager + External Secrets Operator + IRSA) | Installing secret-dependent platform components | OPEN | TBD |
| Upstream app commit pinned (`docs/app-baseline.md`) | App implementation | OPEN | TBD |
| Full lifecycle scripts proven against actual owned EKS/ALB/PVC resources | Routine disposable EKS session operation | OPEN | TBD |
| Portfolio repo and mkdocs.yml nav inspected | Public portfolio integration | OPEN | TBD |

## C. Proposed addition — DEV / QA / PROD-like promotion (not yet deployed)
The owner has expressed the intention to demonstrate promotion using **one disposable EKS cluster**, separated by namespace, Helm values, Argo CD applications and environment-specific data credentials. Start with DEV. Implement QA and PROD-like **after** the DEV application and safe create/destroy process are verified; do not assume hard tenant isolation or separate production-grade clusters. PROD-like promotion requires an explicit human-controlled gate and verified Git revert recovery. Finalise and approve identity, quotas, database separation, capacity, ingress/cost and promotion rules **before** implementation. This is a proposal, not a deployed or tested decision.

## D. Locked-sizing clarification
Locked target remains initially **two t3.medium managed nodes** (see decision 5). Their suitability is unverified: evaluate allocatable CPU/memory and observability overhead; any resizing requires an approved decision update. Do not infer capacity from the target alone.

## E. Approved Stage 1C private-only DEV acceptance — 2026-10-20

**Decision:** Private-only DEV integration is approved for
Stage 1C. Existing frontend and backend ClusterIP Services
remain unchanged. An external ALB is not required for the
accepted internal DEV integration checkpoint.

**Rationale:** Internal frontend → Nginx → FastAPI → RDS
integration has been demonstrated through temporary localhost
port-forwarding. Provisioning an ALB solely for this DEV
acceptance would introduce additional AWS runtime cost,
network exposure and lifecycle teardown dependencies.

**Verified acceptance:** Frontend HTTP, Nginx `/api/` proxy,
FastAPI liveness/readiness/metrics, five Alembic migrations,
authenticated demo-user login and PostgreSQL-backed Items
create/read/delete with verified disposable Item cleanup.

**Limitations:** This does not establish external DNS, TLS,
ALB listener rules, target-group health or public application
access.

**Deferred requirement:** External ALB routing remains in the
authoritative roadmap and requires separately approved
implementation and acceptance. Private DEV acceptance must
not be represented as external ingress acceptance.

**Preserved controls:** Root Argo CD automatic synchronization
remains disabled. Phase 4 CI/CD, ECR publication opt-in,
Lifecycle Batch 1 safeguards and explicit consequential
operation approvals remain unchanged.

**Evidence:** Private Stage 1C acceptance evidence remains
under `.private-evidence/lifecycle/` and is excluded from
public Git history.
