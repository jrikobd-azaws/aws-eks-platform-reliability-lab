# Udemy Kubernetes troubleshooting course → EKS lab study map

**Source:** [Kubernetes Troubleshooting: Real-World Production Fixes (Jai M, Udemy)](https://www.udemy.com/course/kubernetes-troubleshooting/) — public curriculum reviewed 2026-10-14. Public listing describes 7 sections, 34 lectures and approximately 5h 38m. Access to paid individual lecture material was **not** available to this audit; the mapping uses published titles/descriptions, not a claim to have watched the lessons.

## Study alongside implementation — do not block the AWS foundation

| When to learn | Published course lessons | Relevant original lab cases |
|---|---|---|
| While preparing local Kubernetes practice | Introduction; install Minikube; `kubectl` context/namespace handling | Local preparation (does not prove EKS behaviour) |
| After deploying a sandbox workload | CrashLoopBackOff; ImagePullBackOff; Pending pods | K8S-01, K8S-02, K8S-03 |
| During probe and container health implementation | Startup/readiness/liveness; OOMKilled | K8S-04, K8S-05 |
| During Service/Ingress validation | Services/selectors; DNS; NetworkPolicy; Ingress routing | K8S-06, K8S-07, K8S-08 and part of later cross-layer incidents |
| During persistent storage/identity exercises | PVC/PV binding; ConfigMap/Secret errors; RBAC | K8S-09, K8S-10 plus EKS-specific distinctions |
| After the first ten | Stuck termination; deployment rollout; init containers; eviction; HPA; quota/LimitRange | Useful diagnostic variants and advanced exercises, **not** substitutes for all 43 |

## Recommended learning method
1. Watch one lecture, run its **local** Minikube/Kind reproduction, write down the observed symptoms and diagnostics.
2. Adapt the failure to your own two-image FastAPI application and EKS environment **only after baseline health, safe injection scope, recovery instructions and explicit approval exist**.
3. Create evidence for **your own actual EKS failure**; course videos and someone else's example output are not personal implementation evidence.
4. Replace course-specific local networking, storage and registry steps with reviewed EKS-specific methods (ALB, VPC CNI, IRSA, EBS CSI, ECR) as appropriate.

## Scope boundary
The public curriculum gives strong coverage of common Kubernetes Day-2 failures but does not, by itself, substantiate hands-on EKS add-on management, Terraform drift/state, GitHub OIDC, native Helm release troubleshooting, Argo CD reconciliation, RDS dependencies or all six flagship cross-layer investigations. Implement those from the project roadmap and AWS/tool documentation, not from assumptions about this course.

## DevSecOps/ZenPharma workshop: delivery quality gates

The separate [DevSecOps Real-World Project: Build and Operate CI/CD on EKS](https://www.udemy.com/course/devops-workshop/) (AR Shankar; 11 sections, 75 lectures, 9h 5m, public curriculum checked 2026-10-14) is the **same ZenPharma reference project** already audited. Study Terraform PR plan/approval alongside Stage 1A/1B; SonarCloud, GitHub Actions lint/test, two-image Docker build and Trivy while implementing Stage 1C; Helm/Argo/DEV verification during application delivery; and digest promotion/rollback at Stage 1D. The mandatory tests and expected evidence are defined in [`TESTING-STRATEGY.md`](TESTING-STRATEGY.md). Do not copy nine-service-specific tests or tutorial credentials into our two-image project.
