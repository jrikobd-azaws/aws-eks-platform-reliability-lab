# Session workflow — v3 consolidated starter (not yet deployed)

## During any coding task
1. **PLAN, read-only:** Copilot reads `docs/V3-SAFETY-CONTRACT.md`, `docs/SESSION_STATE.md`, `docs/DECISIONS.md`, roadmap and relevant implementation files. Lists exact changes/tests/cost and asks for approval.
2. **APPROVE:** Human approves bounded local modifications explicitly; this is not blanket AWS permission.
3. **IMPLEMENT:** Only the approved source changes. Agents do not execute external writes or lifecycle operations.
4. **VALIDATE:** Run local required checks; missing tools/checks are blockers. Capture failures honestly.
5. **RECORD:** Update `docs/SESSION_STATE.md` with observed facts and unverified claims; no raw account data/secrets in Git.

## Current Stage 0 behaviour
`scripts/start.sh`, `stop.sh`, `validate-up.sh`, `validate-down.sh`, `preflight.sh`, and old `lib.sh` helpers are **blocked/not validated**. No start/stop example from the original v2 package is executable proof. Do not set `LAB_HUMAN_APPROVED`; an environment variable cannot supply security isolation.

## Future disposable EKS session (design only — not an executable runbook yet)
1. Verify the exact AWS account/Region/cluster and backend/workspace, with expected ownership tags and known resource IDs.
2. Human reviews a saved Terraform plan and individually authorises provisioning; confirm target and quotas/cost.
3. Validate infrastructure, platform and application health separately, including an end-to-end application transaction when the app exists.
4. Inject **one** approved fault at a time in the specified DEV/sandbox scope, with baseline evidence and exact recovery.
5. Export raw diagnostics privately to `.private-evidence/`, sanitise only approved publication copies.
6. Stop GitOps reconciliation safely, remove owned Ingress and dependent resources while controllers are available; use a reviewed destroy plan and direct AWS service inventory to verify cleanup.
7. Failed/denied API reads or unexpected resources mean **UNKNOWN/FAIL**, not clean. Use an approved cluster-outage recovery plan as needed.
8. Update session state and billing status with observed facts; do not assert zero spend based on teardown alone.

## Testing sequence for lifecycle scripts
Local mocks and wrong-target checks → tiny owned disposable-resource fixture after remote state foundation → infrastructure/platform validations → full EKS/ALB/PVC/RDS lifecycle on a dedicated disposable stack. Enable scripts **only** after separate approved implementation and observed proof. See `docs/IMPLEMENTATION-GUIDE.md` and `docs/V3-SAFETY-CONTRACT.md`.
