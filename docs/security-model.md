# Security baseline
- GitHub Actions -> AWS OIDC role; no long-lived keys.
- Least-privilege IAM; IRSA roles scoped per service account.
- Kubernetes RBAC and scoped service accounts; NetworkPolicy where the CNI enforces it.
- No secrets in Git; Secrets Manager (or approved pattern) for DB credentials.
- Encrypted, access-restricted Terraform state.
- RDS private; restricted security groups.
- ECR scan on push; no `latest`; pinned images.
- Sanitise published evidence (no account IDs, ARNs with IDs, tokens).
- gitleaks before every push. AI agents use a plan-only profile (docs/access-model.md).
