# D1 Scenario Coverage and Remaining Gaps

Source: `scenarios/SCENARIO_INDEX.md`. `Registered` means the scenario ID is indexed; generic text detection is not a complete scenario test. `Collector`, `Regression`, and `Live` below refer to scenario-level validation, not shared-domain tests.

| ID | Wave | Name | Registered | Generic signal rule | Scenario collector verified | Scenario regression passed | Live validated | Docs published |
|---|---:|---|---|---|---|---|---|---|
| K8S-01 | 1 | CrashLoopBackOff | Yes | Gap | No | No | No | No |
| K8S-02 | 1 | ImagePullBackOff / ErrImagePull | Yes | Candidate | No | No | No | No |
| K8S-03 | 1 | Pending / Scheduling | Yes | Candidate | No | No | No | No |
| K8S-04 | 1 | OOMKilled | Yes | Gap | No | No | No | No |
| K8S-05 | 1 | Liveness/Readiness/Startup probe failure | Yes | Candidate | No | No | No | No |
| K8S-06 | 1 | Service / EndpointSlice failure | Yes | Gap | No | No | No | No |
| K8S-07 | 1 | CoreDNS / DNS resolution | Yes | Candidate | No | No | No | No |
| K8S-08 | 1 | NetworkPolicy failure | Yes | Gap | No | No | No | No |
| K8S-09 | 1 | PVC / StorageClass failure | Yes | Candidate | No | No | No | No |
| K8S-10 | 1 | RBAC / ServiceAccount failure | Yes | Candidate | No | No | No | No |
| K8S-11 | 2 | Node NotReady | Yes | Candidate | No | No | No | No |
| K8S-12 | 2 | ResourceQuota exceeded | Yes | Candidate | No | No | No | No |
| K8S-13 | 2 | Failed Deployment / rollout / rollback | Yes | Gap | No | No | No | No |
| EKS-01 | 2 | IRSA / Pod Identity failure | Yes | Candidate | No | No | No | No |
| EKS-02 | 2 | VPC CNI / IP allocation failure | Yes | Candidate | No | No | No | No |
| HELM-01 | 3 | Helm invalid values | Yes | Gap | No | No | No | No |
| HELM-02 | 3 | Helm template rendering failure | Yes | Candidate | No | No | No | No |
| HELM-03 | 3 | Helm failed upgrade | Yes | Candidate | No | No | No | No |
| HELM-04 | 3 | Helm stuck release (pending-upgrade) | Yes | Candidate | No | No | No | No |
| ARGO-01 | 3 | Argo CD OutOfSync / manual drift | Yes | Candidate | No | No | No | No |
| ARGO-02 | 3 | Argo CD Synced but Degraded | Yes | Gap | No | No | No | No |
| ARGO-03 | 3 | Argo CD invalid manifest | Yes | Candidate | No | No | No | No |
| ARGO-04 | 3 | Argo CD repository authentication failure | Yes | Candidate | No | No | No | No |
| ARGO-05 | 3 | Argo CD sync ordering / dependency | Yes | Gap | No | No | No | No |
| ARGO-06 | 3 | Bad release, Git revert, reconciliation | Yes | Gap | No | No | No | No |
| TF-01 | 4 | Terraform state lock / concurrency | Yes | Candidate | No | No | No | No |
| TF-02 | 4 | Terraform drift | Yes | Gap | No | No | No | No |
| TF-03 | 4 | OIDC / AssumeRole failure | Yes | Candidate | No | No | No | No |
| TF-04 | 4 | Dependency / ordering failure | Yes | Gap | No | No | No | No |
| TF-05 | 4 | Provider / lock-file conflict | Yes | Candidate | No | No | No | No |
| TF-06 | 4 | Partial apply / state mismatch | Yes | Candidate | No | No | No | No |
| TF-07 | 4 | Unexpected replacement | Yes | Gap | No | No | No | No |
| TF-08 | 4 | Terraform import / state reconciliation | Yes | Gap | No | No | No | No |
| TF-09 | 4 | Remote backend failure | Yes | Candidate | No | No | No | No |
| INC-01 | 5 | Argo Degraded to ImagePullBackOff to ECR/IAM | Yes | Candidate | No | No | No | No |
| INC-02 | 5 | App unavailable though pods Running | Yes | Candidate | No | No | No | No |
| INC-03 | 5 | Terraform pipeline failure across OIDC/IAM | Yes | Candidate | No | No | No | No |
| INC-04 | 5 | HTTP 502 with healthy-looking workloads | Yes | Candidate | No | No | No | No |
| INC-05 | 5 | Terraform succeeds but pods Pending | Yes | Candidate | No | No | No | No |
| INC-06 | 5 | Intermittent 504 under load | Yes | Candidate | No | No | No | No |
| EKS-03 | 6 | EBS CSI failure | Yes | Candidate | No | No | No | No |
| EKS-04 | 6 | AWS Load Balancer Controller failure | Yes | Gap | No | No | No | No |
| EKS-05 | 6 | EKS version / add-on compatibility (1.36 to 1.37) | Yes | Gap | No | No | No | No |

Total registered: 43/43. Candidate signal ID links: 28/43. Scenario-specific end-to-end collectors/tests: 0/43. Live validated: 0/43. D1 domain-level tests are distinct from scenario acceptance. Historical cases: `scenarios/D1_HISTORICAL_INVENTORY.json`. D2/D3 cover live and six-wave validation.
