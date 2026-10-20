# Testing and release quality gates — required implementation strategy

**Status:** Approved scope for *incremental implementation*; testing workflows, application tests and live AWS results have **not** been implemented by adding this document. This strategy is a required part of each stage, not a separate pre-EKS project. It supplements rather than overrides `V3-SAFETY-CONTRACT.md`, `DECISIONS.md` or the requirement for explicit human approval before cloud writes.

## Reference and adaptation

Study reference: [DevSecOps Real-World Project: Build and Operate CI/CD on EKS](https://www.udemy.com/course/devops-workshop/) (AR Shankar, public curriculum reviewed 2026-10-14). It is the **ZenPharma-based course** already used as a procedural reference, not a second independent architecture. Its public curriculum explicitly describes GitHub Actions lint/test, SonarCloud code analysis, Trivy container scans, Docker builds, Terraform plan review, GitOps deployment, environment promotion and rollback. The course's original nine-service Java/Node/React application is **not** the application's scope here. Use the existing two-image frontend/FastAPI/PostgreSQL architecture. We have inspected the public syllabus and user-provided ZenPharma repositories; do not claim to have completed course exercises or watched gated videos.

Additional tests below (not all explicitly described by the public workshop syllabus) are required because this lab uses Python orchestration, FastAPI and RDS. The Kubernetes troubleshooting Udemy course is mapped separately in `TRAINING-MAP.md`.

## Common rules for all required gates

1. **Required means blocking when its stage exists.** A missing required tool, missing expected test, zero discovered workloads, failed parser, inaccessible AWS API, failed quality gate or failed scan cannot be silently treated as success. During earlier stages, do not demand tests for source that has not been written yet; record them as *not yet applicable*, not as passed.
2. **Pull requests never apply Terraform or deploy to AWS.** Run static checks, unit tests, scans and plan; a human inspects saved plans, exact target identity and writes, and approves each apply separately. Do not copy ZenPharma's less-restricted write workflows.
3. **Risk and vulnerability findings must be acted on.** Establish an explicit, documented initial severity/quality policy when each scanner is installed. An unresolved blocking finding fails the gate; an exception requires recorded justification, expiry and human approval. Do not use `continue-on-error: true` or `exit-code: 0` to make a designated release-blocking scanner green.
4. **Immutable release identity.** Build each of the two images once per reviewed source revision, scan that artifact and promote the **same digest** DEV -> QA -> PROD-like. Do not re-build or silently change an image between environments.
5. **A green CI run is not sufficient proof of a healthy application.** Post-deploy acceptance requires named workloads, readiness, service responses and the real PostgreSQL transaction where relevant.
6. **Deliberate incident injection is not part of normal release CI.** The 43 scenario scripts are separately approved and scoped to the designated disposable/sandbox workload. Do not inject platform-wide failures while shared QA/PROD-like workloads are operating.
7. **Evidence is from executed tests.** Save workflow run URL/ID, commit and image digest, test counts, failing case, corrected commit, observed outputs, approved release reference and sanitised recovery evidence. Keep raw logs and secrets private.
8. Pin GitHub Actions by verified full commit SHA. Use OIDC with minimally scoped role trust and permissions, never persistent AWS keys in GitHub.

## Required gates by implementation stage

| Stage / change | Gate to implement with the component | Minimum acceptance evidence |
|---|---|---|
| Stage 0: repository and Python safety | `check-v3-patch.py`, 43-ID scenario checker, Bash syntax/ShellCheck, Gitleaks; later `pytest` for wrong account/region/cluster/repo, approval denial, command timeout, API failure/UNKNOWN, dry-run | Local and CI checks pass. A bare Enter and wrong identity produce nonzero failure before writes; launcher exit 78 remains unchanged until separately authorised |
| Stage 1A/1A-F: foundation & fixture | Terraform fmt/validate; IaC scan (Trivy config or an approved equivalent); saved plan review; fixture positive and negative ownership/inventory/deletion tests | Saved plan and approval recorded; failure/AccessDenied/timeout is not misreported CLEAN; fixture verified gone using direct API |
| Stage 1B: EKS, RDS, network | Per-root Terraform fmt/validate/plan/IaC scan; AWS account/cluster ARN and node/network/IAM/RDS inventory; actual destroy/recreate; no unrelated resource deletion | Actual known resource IDs, nodes ready, correct SG/RDS exposure, teardown & partial-failure proof; no guessed availability or spend |
| Stage 1B-P/1B-R: Helm controllers & lifecycle | Helm lint/template/schema check for each new chart; rollout checks, secret/identity validation, health, Python bootstrap unit tests and stage-aware recovery tests | Expected controllers found and Ready; absent or unhealthy components fail; rollback and exact-owned-resource cleanup are proven |
| Stage 1C: frontend & FastAPI | Frontend lint/unit tests using the chosen framework; FastAPI unit/API tests with `pytest` and HTTPX; PostgreSQL integration tests using isolated test DB; build two Docker images; SonarCloud/SonarQube Cloud quality gate subject to verified private-repo entitlement; Trivy repository/dependency + both image scans | Unit/integration tests and configured quality gate pass before ECR publishing; a deliberately failing test and blocking scan rule each stop CI; container images function locally; release references immutable |
| Stage 1C: Helm, Argo, DEV release | Helm lint/template and schema checks; manifest/probe/security policy validation; named Argo Application Synced **and** Healthy; ClusterIP frontend/Nginx/FastAPI/RDS integration using temporary localhost-only port-forwarding and real controlled application database writes | Internal DEV acceptance requires frontend/API readiness, authenticated Items create/read/delete, verified cleanup, root manual-sync protection and recorded controller drift. External ALB routing remains a separately approved ingress acceptance requirement, not an internal DEV prerequisite |
| Stage 1D: observability | Validated alerts, startup dashboards, log/metrics export, recovery validators, safe session teardown; monitoring footprint measured | At least representative injected signals trigger expected alerts; sanitised evidence; no false CLEAN when inventory is UNKNOWN |
| Stage 1D: QA -> PROD-like | Automated QA smoke/integration/regression checks, same digest comparison, human approval, manual PROD-like sync, GitOps revert/rollback verification | Failing QA checks block promotion; same digest verified in every target; human decision and recovered healthy revision recorded |
| Waves 1–6 | Baseline health, controlled fault, diagnostic tests, root cause, smallest approved fix, technical **and user-facing** recovery, prevention | All 43 original IDs retained; every case has actual results and one clean recovery before another injection |

## Reference CI/CD workflow (target architecture, not a deployed pipeline)

- **PR: Terraform:** formatting -> init without writes to resources -> validate -> IaC scan -> approved plan (no apply). Sensitive saved plans are private and access-controlled.
- **PR: Application/Python:** lint -> unit tests -> integration tests (a temporary test PostgreSQL is acceptable) -> code analysis/SonarCloud quality gate -> build ephemeral images -> Trivy checks. No cloud deploy from untrusted PR context.
- **Approved merge/release:** rerun required gates on reviewed revision -> OIDC to ECR -> build or obtain reviewed artifact and scan -> publish immutable digests -> reviewed GitOps DEV change -> Argo reconciliation -> runtime smoke tests. Approval before any authorised external write.
- **Promotion:** verified DEV digest -> approved QA GitOps PR -> QA checks -> separately approved PROD-like GitOps PR -> manual Argo sync -> user-facing smoke -> proven Git revert rollback.

### Notes on tools, timing and resource cost

- Use SonarCloud/SonarQube Cloud **if the actual private-repository plan allows the required analysis/quality gate**. Do not silently skip the gate: document an approved comparable alternative or explicitly record the unresolved blocker when the application CI stage arrives. Avoid running a new permanent SonarQube server solely to reproduce the tutorial.
- The frontend test runner depends on the pinned upstream frontend technology and must not be guessed now. Backend should use FastAPI `pytest`, HTTPX and a disposable isolated PostgreSQL test database; prefer a GitHub Actions service container rather than standing up RDS just for PR tests.
- Trivy can cover source/dependencies, images and IaC. Pin reviewed scanner/action builds; configure severity and exception handling rather than inventing guaranteed vulnerability-free images.
- Keep early workload and performance checks modest. After the healthy DEV baseline, run only an approved *bounded* load/smoke test, collect p95/errors/CPU/memory/RDS response and stop if budget/headroom thresholds are exceeded. This is additional operational evidence, not a reason to block Stage 0 or first VPC provision.
- Keep test code and workflow files small, add them to the repository at the stage where source exists and prove one negative/failing path. Do not create nine services or adopt Java/Maven/Jenkins just to match other examples.

## Evidence record to attach to each stage

For every gate note: `gate ID`, source commit, tool and pinned version, workflow/run ID, expected assertions, *actual* result, negative test result, exceptions with approval/expiry, relevant application digest, resource/cost consequence, what remains unverified. Public MkDocs showcases should link a small number of high-value failure/recovery case studies rather than a wall of green badges. Never describe the lab as production-proven or paid EKS operations.
