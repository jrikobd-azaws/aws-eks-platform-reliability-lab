# Session state (update after each verified working session)

| Field                                    | Value                                                                                                                              |
| ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Last reviewed                            | 2026-10-20 (Task 1B-R: Lifecycle Safety Hardening Batch 1; local validation only)                                                 |
| Last completed infrastructure stage/wave | Phase 4 CI/CD: PHASE4_ACCEPTED; PR #13 merged; infrastructure rebuild recovery validated in PR #14                                  |
| Current task                             | Task 1B-R Batch 1 locally accepted; full lifecycle DR certification remains pending                      |
| Next approved action                     | Review isolated Git changes; then Stage 1C residual application acceptance; full DR remains approval-gated                |
| Scenario progress                        | 0 of 43 done (catalogue retained)                                                                                                  |

## Stage 1C — Private-only DEV integration accepted (2026-10-20)

**Checkpoint:** `STAGE1C_INTERNAL_DATABASE_AND_CRUD_ACCEPTED`.

**Approved decision:** Internal DEV acceptance uses existing
ClusterIP Services. External ALB routing is deferred and has
not been tested or passed.

**Verified application integration:**
- Frontend HTTP 200.
- Nginx `/api/` proxy HTTP 200.
- FastAPI liveness, readiness and metrics HTTP 200.
- `stage3-dev` and `platform-root` previously Synced/Healthy.
- Root Argo CD automatic synchronization disabled.
- AWS Load Balancer Controller Healthy/OutOfSync drift recorded.
- Five Alembic revisions successfully applied to DEV database `app`.
- Final revision `fe56fa70289e`, UUID identifiers, ownership
  constraints and restricted runtime DML permissions verified.
- Dedicated ordinary DEV demo user authenticated and retained.
- PostgreSQL-backed disposable Items create/read/delete passed,
  with cleanup verified.
- Temporary localhost port-forward terminated.

**Private acceptance evidence:**
- `.private-evidence/lifecycle/stage1c-offline-migration-20261020T024535Z/`
- `.private-evidence/lifecycle/stage1c-live-preflight-20261020T024653Z/`
- `.private-evidence/lifecycle/stage1c-migration-role-20261020T024843Z/`
- `.private-evidence/lifecycle/stage1c-live-db-20261020T111945Z/`
- `.private-evidence/lifecycle/stage1c-demo-20261020T112423Z/`

**Troubleshooting incident for MkDocs:**
- Symptom: DEV registration returned HTTP 500.
- Investigation: PostgreSQL raised `UndefinedTable` for `"user"`.
- Root cause: The DEV application schema had not been initialized
  through Alembic.
- Remediation: Five existing migrations rehearsed against
  disposable PostgreSQL and then applied using the approved
  migration identity.
- Validation: Schema revision and constraints verified;
  demo-user authentication and Items CRUD passed.
- Prevention: Lifecycle Batch 2 must validate database identity,
  Alembic revision, required tables, application permissions
  and schema readiness after RDS recreation, before declaring
  application recovery complete.

**Remaining work:** Stage 1D observability; full lifecycle
certification; QA/PROD-like promotion; six troubleshooting
waves covering all 43 scenarios; separately approved external
ALB acceptance; final sanitized MkDocs publication.

**Preserved checkpoints:** `PHASE4_ACCEPTED` and
`BATCH1_MERGED_AND_ACCEPTED`. No changes to CI/CD controls,
ECR publication policy, GitOps root manual synchronization or
destructive-operation approval requirements.

## Current accepted engineering checkpoints

### Batch 1 Lifecycle Safety Hardening (2026-10-20)
- Phase 4 CI/CD: `PHASE4_ACCEPTED` (PR #13 merged, Stage 3 build-and-scan accepted).
- Verified test suite: 79 passing tests (75 baseline + 4 regression tests), 0 failures.
- Hardened `CheckpointStore` stage structure validation to prevent corrupted checkpoint resumption.
- Fixed `CommandRunner` subprocess output fallback ensuring stdout diagnostic capture and secret redaction.
- Enhanced `labctl.py` CLI error reporting to include exception diagnostic messages.
- Full lifecycle disaster recovery remains deliberately incomplete; infrastructure rebuild and DB restore remain guarded.
- Manual Argo CD synchronization policy remains strictly preserved; no autonomous deployments.
- No live AWS operations, Terraform apply/destroy, Kubernetes mutations, or Git pushes executed.

### Historical starter baseline — 14 October 2026

The following records describe the earlier starter repository
and local validation session. They are retained for traceability,
not as statements of current deployed infrastructure or
completed lifecycle functionality.

- v2 skeleton and v3 targeted safety overlay combined into a **new starter copy**.
- Four lifecycle launchers deliberately exit 78; cloud lifecycle functionality is NOT implemented.
- Stage 1A completion is owner-reported; it was not re-queried during this local implementation session.
- Stage 1B completion/deployment is owner-reported; it was not independently queried in this session.
- Stage 2 source adds the platform IAM module, Argo CD/Helm values, GitOps Applications, namespaces, RBAC, quotas, gp3 storage and External Secrets configuration; none was installed or applied in this session.
- No AWS API, Kubernetes API, billing, deployment, Terraform apply/destroy, kubectl mutation, Helm install or Git push was executed in this session.
- Terraform validation was backend-disabled. No live platform readiness, SecretStore synchronization, ALB provisioning or teardown has been proven.

## Accepted decisions since last update
- **No locked architecture decisions changed by the consolidation.**
- QA/PROD-like promotion has been discussed, but implementation requires the owner to record explicit design and approval details in `docs/DECISIONS.md`.

## Deployed resources right now
UNKNOWN — not queried. A local ZIP audit does not prove AWS resources are absent.

## Verified scripts (live proof)
| Script           | Live disposable proof | Notes                 |
| ---------------- | --------------------- | --------------------- |
| start.sh         | NO                    | Intentionally blocked |
| validate-up.sh   | NO                    | Intentionally blocked |
| stop.sh          | NO                    | Intentionally blocked |
| validate-down.sh | NO                    | Intentionally blocked |

## Blocking decisions / unverified items
AWS Region/account/credits; identity isolation; budget/quotas/instance availability; remote-state design; Python safety checks; owned fixture proof; EKS/version/add-on compatibility; complete lifecycle teardown; real application delivery; current AWS spend (billing unknown).

## Evidence and spend
Raw evidence must stay in `.private-evidence/`. Chargeable resource inventory must stay in `.runtime-inventory/` until sanitised. **Spend unknown** — verify in AWS Billing. The tests run while consolidating the local artifact are recorded in `docs/CONSOLIDATION-NOTES.md`.

## Stage 1B local checks (2026-10-14)
- `terraform fmt -recursive infra/terraform`: passed.
- `bash scripts/check-all.sh`: passed; blocked launchers, shell checks, 43-scenario index, Gitleaks, and backend-disabled Terraform validation for permanent, fixture and lab roots passed.
- Trivy IaC scan: skipped because Trivy is not installed; no alternative IaC scanner was available.
- `git diff --check`: passed after normalizing the edited provider file to Unix line endings.
- Live EKS/RDS version and add-on support, quotas, CIDR overlap, costs, saved plan review, deployment and teardown remain unverified and block first apply.
- Exact permanent ECR repository existence/ownership was not checked; verify it before Stage 1C image publishing. This change adds no ECR resources.
- EKS access input is `cluster_admin_principal_arn`, validated for an IAM user or role ARN in the expected account; the access policy remains cluster-admin and creator bootstrap admin remains disabled. Backend-disabled validation passed after this correction; no AWS writes were performed.

## Stage 2 local checks (2026-10-14)
- `terraform fmt -recursive infra/terraform`: passed.
- `bash scripts/check-all.sh`: passed; shell checks, Gitleaks, all 43 scenario IDs, offline platform YAML/IAM invariants, and backend-disabled validation of permanent, fixture and lab roots passed.
- `scripts/validate-platform.py`: passed for 19 YAML files; this checks syntax, IAM policy JSON/size/tag guards, StorageClass parity, namespace/AppProject scope, disabled optional integrations and manual secret sync, not Kubernetes API schemas or Helm chart rendering.
- Helm, kubectl, kubeconform, kube-linter and Trivy are unavailable in this environment; chart render, Kubernetes API validation and IaC scan were not run.
- Stage 1B deployment and cluster state are owner-reported; no AWS or Kubernetes reads were made to independently confirm them.
- Human must review/apply the Stage 2 IAM plan, supply exact Terraform outputs through an approved private configuration path, verify repository privacy/access, and manually bootstrap before controller Applications can sync. The sample GitOps values intentionally retain `REPLACE_WITH_TERRAFORM_OUTPUT_*` markers until then.
