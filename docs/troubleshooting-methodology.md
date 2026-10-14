# Standard troubleshooting method
1. Establish impact and scope
2. Identify the failing service/path
3. Determine likely failure domain (GitHub Actions / Terraform-AWS / EKS-Kubernetes / Helm / Argo CD / App / RDS)
4. Gather evidence BEFORE changing anything
5. Inspect events, logs, metrics, configuration
6. Follow dependencies across layers
7. Form and test a root-cause hypothesis
8. Apply the smallest controlled remediation
9. Validate technical recovery
10. Validate service/user recovery
11. Record root cause
12. Define prevention / detection improvement ("what signal should have detected this?")
Avoid random changes and "restart until it works".
Layer path: GitHub Actions -> Terraform/AWS -> EKS/Kubernetes -> Helm -> Argo CD -> Application -> RDS.
