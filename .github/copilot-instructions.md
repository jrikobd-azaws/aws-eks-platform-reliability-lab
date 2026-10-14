# AWS EKS Platform Reliability Lab — consolidated v3 agent instructions

## Read first, in order
1. `docs/V3-SAFETY-CONTRACT.md` (takes precedence over conflicting v2 rules)
2. `docs/SESSION_STATE.md`
3. `docs/DECISIONS.md` and `docs/PROJECT_BRIEF.md`
4. `docs/ROADMAP.md` and `docs/IMPLEMENTATION-GUIDE.md`
5. `docs/TESTING-STRATEGY.md`, `docs/app-baseline.md`, `docs/access-model.md`, `docs/security-model.md`
6. `scenarios/SCENARIO_INDEX.md`

These files are the source of truth. Never assume missing account IDs, Regions, resource IDs, version availability, price information, completed tasks or approvals. Preserve `TBD` where unknown.

## Mandatory workflow
- **PLAN (read only):** propose exact files, changes, dependencies, resource/cost implications, tests, known risks and unresolved decisions. Await explicit human approval.
- **IMPLEMENT:** modify only the approved local scope. Never run AWS, Terraform apply/destroy, Kubernetes mutations, Helm installs, remote Git pushes, GitHub workflow dispatches or lifecycle launchers as an agent.
- **VALIDATE:** run safe local checks; report real outputs, skipped tools and unverified behaviour. Required missing checks are blockers.
- **RECORD:** update session state accurately after approved work, without claiming cloud resources, cost or recovery evidence that was not observed.
- End each task with changed files, checks and results, assumptions, deviations, outstanding blockers and next required human approval.

## Safeguards
- Use an isolated least-privilege read/plan-only identity for agent sessions. Never access or prompt for admin credentials, never set `LAB_HUMAN_APPROVED`, and never silently approve actions. Humans execute every approved external write.
- Never change `docs/DECISIONS.md`, this instruction file, the safety contract, lifecycle scripts, architecture choices or scenario IDs/waves without explicit approval.
- `scripts/start.sh`, `stop.sh`, `validate-up.sh` and `validate-down.sh` intentionally exit 78. Do not enable them merely to make tests pass. They require separately approved design, mocks, a disposable fixture and live tests.
- Validate exact STS account, Region, EKS cluster ARN and API endpoint/CA, Terraform backend and workspace, GitHub identity and owned resource IDs before writes. Unknown or failed API responses never mean absence.
- Never delete account-wide or namespace-wide resources based solely on a name. Preserve unrelated resources; no blind finalizer removal or blind state-lock deletion.
- No long-lived AWS keys in GitHub, no raw evidence, credentials, Terraform state, kubeconfig or secret values committed. Sanitise published evidence.

## Architecture and project scope
- Keep all **43** existing scenarios, IDs, six-wave order and evidence standards. No silent dropping, merging or marking done without actual proof.
- This is an independently built **portfolio lab** — not paid production EKS experience.
- Terraform owns AWS infrastructure; Argo CD + Helm deliver the application. A separate native Helm sandbox is required for Helm release history/rollback scenarios.
- Permanent infrastructure includes state/IAM/OIDC/ECR/budgets; disposable resources include VPC/NAT/EKS/nodes/RDS/workload ALBs. Verify permanent resources can still incur charges.
- Intended core: EKS 1.36 then 1.37 (verify Region/add-ons at build time); initial two-AZ managed nodes targeting 2 x t3.medium, subject to measured headroom; single NAT; single-AZ RDS; IRSA core with Pod Identity exercise; two container images; app-of-apps; trimmed Prometheus/Grafana + CloudWatch.
- Follow `docs/app-baseline.md` for permitted application changes and `scenarios/_TEMPLATE.md` for incident evidence.
- Python `labops` safety modules should be small and testable; manual installation, then Python automation, then proven rebuild. Do not rewrite all ZenPharma scripts as a prerequisite.
- `docs/TESTING-STRATEGY.md` defines **mandatory gates once their stage exists**: PR checks, targeted Python/FastAPI/frontend tests, PostgreSQL integration, SonarCloud (or explicitly approved alternative), Trivy, Helm and GitOps validation, post-deploy smoke tests, QA promotion/rollback and evidence of a blocked failure. Implement tests alongside each component, not as an upfront framework. Never count an unimplemented, skipped or failing gate as PASSED.
- QA and PROD-like GitOps promotion are proposed *after* DEV works and safe teardown is proven. One shared cluster is a simulation, not independent production environments. Obtain a recorded decision before implementing.

## Local checks
- `python3 scripts/check-v3-patch.py`
- `python3 scripts/check_scenarios.py`
- `bash scripts/check-all.sh`
Do not weaken checks or treat missing tools as success. Do not execute the blocked lifecycle launchers as if they were functional deployments.
