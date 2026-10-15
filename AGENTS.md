# AWS EKS Platform Reliability Lab
# Shared AI Agent Instructions

## 1. Project identity

This repository implements an enterprise-aligned
AWS EKS platform reliability and troubleshooting lab.

The project is a documented portfolio implementation,
not evidence of paid production AWS/EKS experience.

## 2. Authoritative documentation

Before planning implementation, read:

- .github/copilot-instructions.md
- docs/V3-SAFETY-CONTRACT.md
- docs/SESSION_STATE.md
- docs/DECISIONS.md
- docs/PROJECT_BRIEF.md
- docs/ROADMAP.md
- docs/app-baseline.md

Read relevant source files and runbooks as required.

The V3 safety contract takes precedence over
conflicting agent instructions.

## 3. Project requirements

- Preserve all 43 troubleshooting scenarios.
- Preserve existing scenario IDs and wave structure.
- Follow approved architecture decisions.
- Keep Terraform responsible for infrastructure.
- Keep Helm and Argo CD responsible for application delivery.
- Preserve existing security and testing requirements.
- Never invent completed work or test results.
- Never weaken checks to make validation pass.

## 4. Agent workflow

PLAN:
Inspect repository context and propose exact changes.
Identify acceptance tests, risks and dependencies.

APPROVAL:
Wait for explicit human approval before implementation.

IMPLEMENT:
Modify only approved files and scope.

VALIDATE:
Run approved local checks and report actual results.

RECORD:
Report changes, successful checks, failures,
unverified behaviour and remaining work.

## 5. Cloud safety

Never autonomously execute:

- terraform apply or destroy
- AWS or Azure resource mutations
- kubectl apply, patch or delete
- Helm installations or upgrades
- GitHub pushes, merges or deployments
- Infrastructure lifecycle scripts
- Failure injection against live resources

Each action requires separate human approval.

Never retrieve, print, commit or disclose credentials,
private keys, secret values or Terraform state.

Do not bypass the V3 safety contract.

## 6. Environment lifecycle

The previous EKS lab deployment was destroyed
after successful teardown verification.

The permanent Terraform backend and dedicated
application Secrets Manager secret were retained.

Do not assume the cloud environment is currently running.

Verify current state before planning live operations.

## 7. Validation

Use existing repository checks, including:

- scripts/check-all.sh
- Terraform formatting and validation
- Python tests
- Helm and Kubernetes manifest validation
- Gitleaks
- GitHub CI

Run checks only within approved scope.

Do not claim cloud validation from local checks.

## 8. Completion report

Every implementation task must report:

1. Files modified
2. Changes implemented
3. Validation actually executed
4. Tests passed and failed
5. Remaining blockers
6. Unverified assumptions
7. Next approval required
