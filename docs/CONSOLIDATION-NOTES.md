# Consolidation notes — 2026-10-14

## Inputs and policy
Combined `eks-lab-repo-pack-v2(1).zip` (complete original scaffold) and `EKS-v3-safety-patch-and-start-guide.zip` (targeted safety overlay) **locally**. Do not mistake ZenPharma sample repos for project source: they are procedural references only. The original v3 ZIP states its safety contract was recreated from agreed requirements rather than recovered byte-for-byte; the owner should review that contract before authorising cloud changes.

## Changes made in the consolidated copy
- Preserved all original v2 architecture, application baseline, runbooks and 43-scenario catalogue, including IDs and waves.
- Imported v3 contract, addendum, safety checker, launchers and blocked-launcher checks. Four life-cycle launchers intentionally remain **not implemented** and return 78.
- Reconciled v2 detail and v3 precedence in one `.github/copilot-instructions.md`; no competing instructions retained.
- Combined ignore rules; private/raw evidence and runtime inventory remain untracked.
- Reconciled `SESSION_STATE.md` to report AWS resources/spend **UNKNOWN**, not absent. Do not claim deployment/teardown proof.
- Corrected the circular Stage 0 gate by moving tiny fixture validation after the permanent state foundation and EKS-specific lifecycle proof after EKS implementation. Kept the locked architecture entries and the wave/publication structure; gate timing clarification is explicitly subject to owner review.
- Added proposed, **not implemented**, QA/PROD-like single-cluster promotion in decisions and guide. No claim of strong environment isolation.
- Added this guide, `START-HERE.md` and public-curriculum-based `TRAINING-MAP.md`.
- Retired unsafe v2 `preflight.sh`/`lib.sh` helpers and incident shell templates (all explicitly blocked), and replaced obsolete runnable session/teardown instructions with design-only runbooks.
- Kept v3 `checks.yml` scope local and adapted to include required safety check and Gitleaks, while Terraform/IaC and Helm gates will need implementation as those source files become real.

## What does NOT exist yet
No Terraform resources or executable cluster automation, deployed EKS, two-image app, controllers, GitOps applications, real health checks, tested CI or live scenario evidence. Later stages must implement and test them. No permissions/Region/account IDs/versions or budget figures are assumed valid for your account.

- Updated local Gitleaks command to `gitleaks dir` so an unpacked starter can be scanned before it has Git history.

## Local test results
See `VALIDATION-LOCAL.txt` in repository root for actual, environment-specific output. Do not consider skipped tools or cloud operations validated.
