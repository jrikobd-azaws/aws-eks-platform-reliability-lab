# Session state (update after each verified working session)

| Field                                    | Value                                                                                                                             |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Last reviewed                            | 2026-10-14 (Stage 1B Terraform source; local validation only)                                                                     |
| Last completed infrastructure stage/wave | Stage 1A reported by owner as deployed, validated and merged; not independently queried in this session                           |
| Current task                             | Stage 1B Terraform source implemented and locally validated; no AWS plan/apply performed                                          |
| Next approved action                     | Human verifies account, Region, CIDR, EKS/add-on support, quotas and RDS orderability; then reviews a saved plan before any apply |
| Scenario progress                        | 0 of 43 done (catalogue retained)                                                                                                 |

## Confirmed baseline facts
- v2 skeleton and v3 targeted safety overlay combined into a **new starter copy**.
- Four lifecycle launchers deliberately exit 78; cloud lifecycle functionality is NOT implemented.
- Stage 1A completion is owner-reported; it was not re-queried during this local implementation session.
- Stage 1B Terraform source exists under `infra/terraform/environments/lab` and the `network`, `eks` and `rds` modules; it is NOT deployed.
- No AWS API, Kubernetes API, billing, deployment, apply or destroy operations were executed in this session.
- Terraform validation was backend-disabled; no live infrastructure readiness or teardown has been proven.

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
