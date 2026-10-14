# v3 safety contract
Authoritative over conflicting v2 safety instructions. All 43 scenarios, existing IDs and six waves remain required.

## Account and target
Before writes verify STS exact expected account, Region, exact cluster ARN and ownership, kubeconfig endpoint/CA against EKS description, backend bucket/key and workspace. Substring context matching is not sufficient. Terraform must restrict allowed account IDs. Separate Project and Stack ownership tags, and record exact resource IDs before cleanup.

## Unknown is not absent
Every AWS/Kubernetes/Terraform read must capture exit status and validate output. AccessDenied, timeout, missing tools, invalid JSON and inaccessible backend mean UNKNOWN/FAIL. Never infer clean teardown from an empty failed API response. Required reads fail closed. Retrying must be bounded and logged.

## Identity separation
Copilot uses a real least-privilege read/plan identity. Never expose an administrator profile, cached admin session or privileged environment to the agent. Plan-only state-lock writes may be necessary but do not justify resource mutation rights. LAB_HUMAN_APPROVED and terminal approvals are friction, not security. Humans approve and run external writes. Never auto-approve deployments.

## Phases and dependencies
Bootstrap local repo and permanent state/IAM first; provision infrastructure next; platform then app next; scenarios after healthy baseline. Do not require app health before infrastructure exists. Region/account/credits are prerequisites to cloud provisioning; MkDocs nav is a publication gate, not a state-bootstrap gate. Requested Kubernetes 1.36/1.37 targets require authoritative availability/support/add-on verification; do not silently substitute.

## Lifecycle
All launchers remain blocked until implemented with tests. Saved plans must be reviewed and approved. Start checks prerequisites and records inventory. Stop exports private evidence, verifies GitOps reconciliation stopped, removes only owned resources, keeps LB/CSI controllers alive for cleanup, reviews destroy plan, destroys ephemeral resources and verifies absence by captured IDs and service APIs. No namespace-wide deletion solely on namespace name. Never delete protected namespaces. Cluster-outage recovery must work independently of healthy Kubernetes. No blind finalizer removal.

## Cleanup proof
Check ALBs/NLBs/target groups, ENIs, NAT/EIPs, EC2/EBS/PVs/reclaim policies, RDS instances/snapshots/retained backups, logs and secrets scheduled for deletion. Explicitly allowlist permanent identities. Tagging API alone is incomplete and can lag. Suspected untagged resources require human review. Unrelated account resources are observations, not automatic deletes. Permanent resources can still cost money.

## Validators
Separate infra/platform/app modes. Require expected counts, pod readiness not merely Running, desired replicas, named Argo applications, RDS AVAILABLE and an end-to-end transaction as appropriate. Missing namespaces or zero applications cannot pass full app validation. Missing required checks cannot pass a release gate.

## Helm and GitOps
Argo normally renders charts, not native Helm release state. HELM-03/04 use a separate native Helm sandbox with history/rollback/pending-release evidence. ARGO-06 uses Git revert and reconciliation. Test actual hook ownership and ordering semantics.

## Scenarios and recovery
One scenario at a time; build K8S-01 as the proven reference first. All 43 required; compact means shorter narrative, not weaker evidence. Bad DB host may cause readiness failure rather than crash; cordon/drain does not equal NotReady; scheduling pod limits do not equal CNI IP exhaustion; ALB failures may be 503/500 rather than 502/504. Observe actual results. Each run needs unique UTC ID, exact scope, abort threshold, prerequisites, known-good recovery and evidence. Do not restore raw get-all YAML wholesale; use reviewed manifests/Git commits and treat secrets privately. Terraform failure drills use isolated sandbox state/roles.

## Capacity, costs and documentation
Two t3.medium nodes are an unverified initial sizing choice. Measure allocatable resources and monitoring overhead; escalate changes for approval. No invented cost or timing claims. Raw evidence is privately staged and sanitized before publication. Code-owner rules are review mechanisms, not local-agent edit locks; solo PR authors cannot count on self-approval. Required checks: syntax, ShellCheck, Gitleaks, scenario-index checker, Terraform fmt/validate/lint/security checks when code exists, Helm lint/render when chart exists, mocked wrong-target/API-error tests, then disposable live tests. Report skipped/unrun checks honestly.
