# Project brief - AWS EKS Platform Reliability Lab
Subtitle: Terraform | Kubernetes | Helm | Argo CD | GitHub Actions | Observability | Failure Injection & RCA

## Purpose
Build one integrated, governed AWS EKS platform and demonstrate systematic Day-2 troubleshooting across AWS/Terraform, Kubernetes/EKS, Helm, Argo CD/GitOps, observability and application dependencies.
Operational question: *A service is broken. Which layer is responsible, how do I prove the root cause, restore service safely, validate recovery and reduce recurrence?*

## Honest labelling (mandatory)
Portfolio implemented / lab validated. Never describe this as paid production AWS or Kubernetes experience. Only describe completed work. Never advertise a scenario count that does not exist yet.

## Architecture (summary)
AWS Organizations -> Control Tower -> governed workload account -> GitHub Actions + AWS OIDC -> Terraform -> VPC/IAM/EKS/ECR/RDS -> EKS platform -> Helm -> Argo CD -> three-tier app -> Prometheus/Grafana/CloudWatch.
App path: Internet -> ALB -> Frontend -> Backend -> RDS PostgreSQL.

## Delivery model
- Infrastructure: Git push -> GitHub Actions -> terraform fmt/validate/plan -> review -> controlled apply.
- Application: Git -> Argo CD -> Helm -> Kubernetes.

## Non-goals
Complex microservices app; shallow scenarios without evidence; CKAD demo; full landing-zone product; AFT before EKS work; self-managed PostgreSQL on Kubernetes; autonomous AI remediation; production-experience claims; screenshot collections without diagnostic context.

## Operating model: ephemeral
Daily ~3.5 hour session. Start -> validate -> work -> stop (export evidence, destroy) -> validate nothing is left billing. Control Tower and the permanent stack stay.
