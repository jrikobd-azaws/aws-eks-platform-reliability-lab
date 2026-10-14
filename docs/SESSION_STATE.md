# Session state (update after each verified working session)

| Field                                    | Value                                                                                                                              |
| ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Last reviewed                            | 2026-10-14 (Stage 2 platform source; local validation only)                                                                        |
| Last completed infrastructure stage/wave | Stage 1B reported by owner as complete, deployed, validated and merged; not independently queried in this session                  |
| Current task                             | Stage 2 platform source implemented and locally validated; no AWS or Kubernetes mutations performed                                |
| Next approved action                     | Human reviews/applies the saved Stage 2 IAM plan, resolves private GitOps identifiers, then follows the platform bootstrap runbook |
| Scenario progress                        | 0 of 43 done (catalogue retained)                                                                                                  |

## Confirmed baseline facts
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
