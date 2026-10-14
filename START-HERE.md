# START HERE — one consolidated EKS lab v3 starter repository

This is the **complete consolidated starter**: v2 project scaffold + the targeted v3 safety patch + reconciled instructions/roadmap. You do **not** need a separate v2 checkout or another overlay ZIP. It is not a deployed system; Terraform/app source remains scaffold-only and four lifecycle launchers intentionally exit **78**.

## 1. Extract once
On Windows, use VS Code with WSL and keep the repository under your WSL home directory (rather than executing Linux scripts directly from Windows). Extract the ZIP to a directory such as `~/projects/aws-eks-platform-reliability-lab/` and open that **repository root** in VS Code. Ensure `.github/` is visible.

## 2. Read the authoritative documents
Read in order: `docs/V3-SAFETY-CONTRACT.md`, `docs/SESSION_STATE.md`, `docs/DECISIONS.md`, `docs/PROJECT_BRIEF.md`, `docs/ROADMAP.md`, `docs/IMPLEMENTATION-GUIDE.md`, **`docs/TESTING-STRATEGY.md`**, `docs/app-baseline.md`, `.github/copilot-instructions.md` and `scenarios/SCENARIO_INDEX.md`.

## 3. Install and verify local tools
Stage 0 requires Bash, Git, Python 3, ShellCheck and Gitleaks. Later stages need Terraform, AWS CLI, kubectl, Helm and eventually Docker, pinned/approved before use. Install tools intentionally; do not ask an AI agent to perform unattended installation or obtain administrator credentials.

## 4. Run local-only checks (safe; no cloud writes)
```bash
python3 scripts/check-v3-patch.py
python3 scripts/check_scenarios.py
bash scripts/check-all.sh
```
`check-v3-patch.py` verifies that all four launchers are still blocked (78). `check-all.sh` must report a failure/blocker if a required tool is missing; do not bypass a failing gate. Terraform/Helm lint and deployment remain unverified until actual source and test tooling exist.

## 5. Initialize or import into Git *only after inspecting contents*
If starting a new repository, create a **private, empty** GitHub repo. In the extracted root:
```bash
git init -b main
git status --short
git add .
git diff --cached --stat
git diff --cached
# Only if staged data is inspected and safe:
git commit -m "chore: establish consolidated v3 safety baseline"
```
Add the actual remote using your own reviewed URL; never paste a placeholder remote. If you already have Git history, reconcile this starter on a separate branch rather than force-pushing or overwriting existing history. Do not commit private credentials, raw evidence, `.tfvars` or Terraform state.

## 6. Copilot's *first* bounded task
In VS Code Copilot planning mode, with terminal auto-approval **disabled** and an isolated plan-only identity, request:

> Read the safety contract, decisions, session state, roadmap and Copilot instructions in the current repository. Inspect files only. Propose Stage 0 local baseline verification: exact files, prerequisite tools, missing checks, mocked wrong-account/wrong-cluster/failed-API cases, and acceptance tests. Make no edits and run no AWS/Kubernetes/GitHub writes until I approve the plan. Preserve all 43 scenarios and the blocked lifecycle launchers. End with: What did you assume, what did you actually test, and what remains unverified?

## 7. Then implement in dependency order
Stage 0 local baseline → 1A governance, remote state/OIDC → 1A-F tiny owned fixture → 1B manually provisioned EKS/RDS → 1B-P manual controllers then Python bootstrap → 1B-R full rebuild/teardown proof → 1C two-image application/Helm/DEV GitOps → 1D monitoring/controlled QA-PROD-like promotion → six troubleshooting waves → verified portfolio publications.

See `docs/IMPLEMENTATION-GUIDE.md` and `docs/TRAINING-MAP.md`. **Do not run** `scripts/start.sh`, `scripts/stop.sh` or the retired `scripts/preflight.sh` expecting functional deployment; they deliberately stop at the safety gate.
