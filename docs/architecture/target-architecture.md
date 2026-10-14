# Target architecture
```text
AWS Organization -> Control Tower -> Workload account
GitHub Actions --OIDC--> IAM role --> Terraform
VPC (2 AZ): public subnets (ALB, NAT) / private subnets (nodes, RDS)
EKS 1.36 (managed nodes x2, t3.medium) + ECR + RDS PostgreSQL (single-AZ)
Argo CD -> Helm -> frontend, backend, seed Job
Prometheus/Grafana + CloudWatch
```
## Network (CIDRs TBD; must not overlap 10.20.x, 10.100-120.x, 10.200-220.x)
VPC TBD; 2 public subnets (ALB, NAT); 2 private subnets (nodes, RDS subnet group); one NAT gateway (documented trade-off); RDS private, SG allows backend/node SG only.

## Stacks
- `infra/terraform/permanent`: state bucket and locking, GitHub OIDC provider/role, ECR repos, evidence bucket, budget alarms.
- `infra/terraform/environments/lab`: network, EKS, node group, IRSA roles, RDS, LB controller IAM, Argo CD bootstrap inputs.
Modules: `infra/terraform/modules/{network,eks,ecr,iam,rds}`.

## Documented, not implemented
DR Region and cross-Region backup; multi-AZ RDS; NAT per AZ; Karpenter/autoscaling; production node sizing.
