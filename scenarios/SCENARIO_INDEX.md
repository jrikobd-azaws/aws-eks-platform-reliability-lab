# Scenario index - 43 scenarios, all in scope

All 43 scenarios are committed. There is no tiering. Order is set by **wave** and only the project owner may change it.
Status values: `planned`, `in-progress`, `done`. A scenario is `done` only when it meets scenarios/_TEMPLATE.md and the Evidence column names a real evidence path.
Depth: `full` = complete evidence chain with extended analysis; `compact` = complete evidence chain, shorter write-up. Both count as done.
IDs are permanent. Never renumber. Never advertise a scenario count until that many are `done`.
Waves: 1 core Kubernetes | 2 advanced Kubernetes/EKS | 3 Helm and Argo CD | 4 Terraform | 5 cross-layer incidents | 6 EKS platform changes.

## Catalogue (machine-checked by scripts/check_scenarios.py)
| ID | Scenario | Wave | Depth | Status | Evidence |
|---|---|---:|---|---|---|
| K8S-01 | CrashLoopBackOff | 1 | full | planned | - |
| K8S-02 | ImagePullBackOff / ErrImagePull | 1 | full | planned | - |
| K8S-03 | Pending / Scheduling | 1 | full | planned | - |
| K8S-04 | OOMKilled | 1 | full | planned | - |
| K8S-05 | Liveness/Readiness/Startup probe failure | 1 | full | planned | - |
| K8S-06 | Service / EndpointSlice failure | 1 | full | planned | - |
| K8S-07 | CoreDNS / DNS resolution | 1 | full | planned | - |
| K8S-08 | NetworkPolicy failure | 1 | full | planned | - |
| K8S-09 | PVC / StorageClass failure | 1 | full | planned | - |
| K8S-10 | RBAC / ServiceAccount failure | 1 | full | planned | - |
| K8S-11 | Node NotReady | 2 | full | planned | - |
| K8S-12 | ResourceQuota exceeded | 2 | compact | planned | - |
| K8S-13 | Failed Deployment / rollout / rollback | 2 | full | planned | - |
| EKS-01 | IRSA / Pod Identity failure | 2 | full | planned | - |
| EKS-02 | VPC CNI / IP allocation failure | 2 | full | planned | - |
| HELM-01 | Helm invalid values | 3 | full | planned | - |
| HELM-02 | Helm template rendering failure | 3 | compact | planned | - |
| HELM-03 | Helm failed upgrade | 3 | full | planned | - |
| HELM-04 | Helm stuck release (pending-upgrade) | 3 | compact | planned | - |
| ARGO-01 | Argo CD OutOfSync / manual drift | 3 | full | planned | - |
| ARGO-02 | Argo CD Synced but Degraded | 3 | full | planned | - |
| ARGO-03 | Argo CD invalid manifest | 3 | compact | planned | - |
| ARGO-04 | Argo CD repository authentication failure | 3 | full | planned | - |
| ARGO-05 | Argo CD sync ordering / dependency | 3 | compact | planned | - |
| ARGO-06 | Bad release, Git revert, reconciliation | 3 | full | planned | - |
| TF-01 | Terraform state lock / concurrency | 4 | full | planned | - |
| TF-02 | Terraform drift | 4 | full | planned | - |
| TF-03 | OIDC / AssumeRole failure | 4 | full | planned | - |
| TF-04 | Dependency / ordering failure | 4 | compact | planned | - |
| TF-05 | Provider / lock-file conflict | 4 | full | planned | - |
| TF-06 | Partial apply / state mismatch | 4 | full | planned | - |
| TF-07 | Unexpected replacement | 4 | full | planned | - |
| TF-08 | Terraform import / state reconciliation | 4 | full | planned | - |
| TF-09 | Remote backend failure | 4 | compact | planned | - |
| INC-01 | Argo Degraded to ImagePullBackOff to ECR/IAM | 5 | full | planned | - |
| INC-02 | App unavailable though pods Running | 5 | full | planned | - |
| INC-03 | Terraform pipeline failure across OIDC/IAM | 5 | full | planned | - |
| INC-04 | HTTP 502 with healthy-looking workloads | 5 | full | planned | - |
| INC-05 | Terraform succeeds but pods Pending | 5 | full | planned | - |
| INC-06 | Intermittent 504 under load | 5 | full | planned | - |
| EKS-03 | EBS CSI failure | 6 | full | planned | - |
| EKS-04 | AWS Load Balancer Controller failure | 6 | full | planned | - |
| EKS-05 | EKS version / add-on compatibility (1.36 to 1.37) | 6 | full | planned | - |

## Failure-injection details
Each injection is a script under scripts/inject/ID.sh with a matching scripts/recover/ID.sh, built from the templates in those folders. Detailed steps are written only when that scenario is built.

| ID | Failure injection | Primary evidence | Root cause and fix | Detection signal | Blast radius and safety |
|---|---|---|---|---|---|
| K8S-01 | Set invalid required env var (e.g. DB host) via Helm values | describe pod; logs; logs --previous | Config error; correct values and roll out | Restart-rate alert | app namespace only |
| K8S-02 | Point image to a non-existent ECR tag | Pod events; ECR describe-images; pull permissions | Wrong tag or pull permission; correct | Pods-not-ready alert | app namespace only |
| K8S-03 | Request more CPU/memory than any node can allocate | describe pod (FailedScheduling); node allocatable | Right-size requests/selectors | Unschedulable-pod alert | app namespace only |
| K8S-04 | Set backend memory limit too low | describe pod (OOMKilled, exit 137); kubectl top | Raise limit/request | OOM/restart alert | app namespace only |
| K8S-05 | Wrong probe path/port or aggressive timings | Events; Running but 0/1 Ready | Fix probe | Readiness-failure alert | app namespace only |
| K8S-06 | Break Service selector or targetPort | get endpointslices; pod labels | Fix selector/port | Synthetic HTTP check | app namespace only |
| K8S-07 | Break service FQDN in config (preferred); CoreDNS scale-down variant only with snapshot and recover script | nslookup/dig from pod; CoreDNS logs | Fix name or restore CoreDNS | DNS-error alert | CoreDNS variant is cluster-wide; config variant is app namespace only |
| K8S-08 | Apply over-restrictive policy | Trace DNS-Service-Endpoint-Policy | Add minimal allow rule | Connection-failure alert | Prerequisite: CNI network policy enforcement enabled |
| K8S-09 | Wrong StorageClass or access mode | describe pvc; events; StorageClass | Fix class/mode | PVC-pending alert | Prerequisite: EBS CSI driver installed |
| K8S-10 | Remove a Role rule from the app service account | kubectl auth can-i; bindings | Restore least-privilege rule | Forbidden-error alert | app namespace only |
| K8S-11 | Cordon/drain variant first; harder variant stops kubelet on ONE node via SSM | describe node conditions; kubelet logs; node events | Restore kubelet or replace node | NodeNotReady alert | Halves capacity: only with 2 healthy nodes; abort if app unhealthy beyond limit |
| K8S-12 | Set namespace quota below workload requests | Forbidden exceeded-quota events; describe quota | Raise quota or reduce requests | Quota-usage alert | app namespace only |
| K8S-13 | Deploy bad image or command to a plain (non-Helm) test Deployment | rollout status/history | kubectl rollout undo and fix | Deployment-unavailable alert | test Deployment only |
| EKS-01 | Break SA annotation or trust-policy audience/subject; then repeat with Pod Identity | AccessDenied/WebIdentity errors; IAM trust; CloudTrail | Fix trust/annotation/association | AccessDenied alert | test service account only |
| EKS-02 | Scale pods beyond node pod/ENI capacity or constrain subnet IPs | FailedCreatePodSandBox events; aws-node logs | Scale nodes / prefix delegation / subnet | IP-availability metric | app namespace; may starve other pods |
| HELM-01 | Wrong value type or missing env value | helm lint/template; rollout events | Fix values | Chart lint in CI | app release |
| HELM-02 | Broken template or missing required value | helm lint; helm template --debug | Fix template | Chart lint in CI | local only |
| HELM-03 | Upgrade with immutable-field change or bad image | helm history/status; events | Roll back or fix forward | Release-failed alert | app release |
| HELM-04 | Interrupt an upgrade | helm status; release secrets | Roll back; last resort clear stuck state | Release-pending alert | app release |
| ARGO-01 | kubectl edit a live resource | argocd app diff | Revert or commit change | OutOfSync alert | app |
| ARGO-02 | Git-valid manifest whose pods fail | App health vs sync status | Fix workload | Degraded alert | app |
| ARGO-03 | Schema-invalid YAML in Git | Sync error message | Fix manifest | Sync-failed alert | app |
| ARGO-04 | Expire or revoke the repo credential (test repo) | Repo connection status; repo-server logs | Rotate credential | Repo-error alert | test app repo |
| ARGO-05 | CRD/CR or hook ordering without sync waves | Sync errors | Sync waves/hooks | Sync-failed alert | app |
| ARGO-06 | Merge a bad commit; git revert; let Argo reconcile | argocd history; git log | git revert and sync | Error-rate alert | app |
| TF-01 | Hold a lock in one session, run a second | Lock error and lock info | Wait; force-unlock only after verifying owner | Pipeline failure alert | sandbox state key only |
| TF-02 | Change a security group or tag in the console | terraform plan diff | Restore or codify | Scheduled drift check | non-critical lab resource |
| TF-03 | Break the trust policy subject/audience on a sandbox role | Actions log; AssumeRoleWithWebIdentity error; CloudTrail | Fix trust policy | Pipeline failure alert | sandbox role, never the real apply role |
| TF-04 | Create an ordering race between IAM policy and consumer | Apply error; terraform graph | Fix references, not blanket depends_on | CI failure | sandbox stack |
| TF-05 | Mismatch provider versions between machines/CI | init errors; lock file diff | Controlled upgrade; commit lock file | CI failure | sandbox stack |
| TF-06 | Force a mid-apply failure with an invalid parameter | state list vs AWS vs plan | Reconcile and re-apply | CI failure | sandbox stack |
| TF-07 | Change a ForceNew attribute | Plan shows -/+ | Stop before apply; redesign | Plan-review gate | plan only, never apply |
| TF-08 | Create a resource manually, then bring it under Terraform | Plan shows create for existing resource | Import block, then no-op plan | Plan diff | sandbox stack |
| TF-09 | Revoke backend permission or point at wrong bucket | init/plan backend errors | Restore permissions/config | CI failure | sandbox backend ONLY, never the lab state bucket |
| INC-01 | Combine bad image reference with missing ECR permission | Argo status, events, ECR, IAM | Trace layers to root cause | Degraded and pull-failure alerts | app |
| INC-02 | Remove backend-to-RDS security group rule | Readiness, endpoints, RDS connectivity | Restore rule | Readiness and synthetic alerts | app and RDS SG |
| INC-03 | Combine trust and permission faults on sandbox role | Actions log, IAM, CloudTrail | Fix trust then permission | Pipeline failure alert | sandbox role |
| INC-04 | Break backend DB config so ALB targets fail | ALB target health, backend logs, RDS | Fix connection config | Target-health alarm | app |
| INC-05 | Shrink node group below workload requests | Scheduler events, node capacity | Right-size node group | Unschedulable alert | node group in lab stack |
| INC-06 | Load test against a slow query or small connection pool | ALB metrics, backend latency, RDS CPU/connections | Fix pool or index (modest tuning) | Latency and connection alerts | app and RDS |
| EKS-03 | Remove CSI IAM permission or break the add-on | PVC Pending; CSI controller logs | Fix IAM/add-on | PVC-pending alert | affects new volumes only |
| EKS-04 | Remove controller IAM permission or subnet tag | Ingress events; controller logs; no ALB | Fix IAM/tags | Ingress-no-address alert | affects new/updated ingress |
| EKS-05 | Upgrade control plane then nodes and add-ons | Upgrade insights; deprecated APIs; add-on versions | Update add-ons/manifests | Deprecation insights | Whole cluster; control plane cannot be downgraded; rebuild at 1.36 to re-test |
