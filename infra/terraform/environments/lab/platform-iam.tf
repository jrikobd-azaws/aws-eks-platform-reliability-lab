module "platform_iam" {
  source = "../../modules/iam"

  expected_account_id     = var.expected_account_id
  cluster_name            = module.eks.cluster_name
  cluster_oidc_issuer_url = module.eks.cluster_oidc_issuer_url
  app_runtime_secret_arn  = var.app_runtime_secret_arn
  tags                    = local.lab_tags
}

output "aws_load_balancer_controller_role_arn" {
  description = "IRSA role ARN for kube-system/aws-load-balancer-controller."
  value       = module.platform_iam.aws_load_balancer_controller_role_arn
}

output "dev_secrets_reader_role_arn" {
  description = "IRSA role ARN for dev/external-secrets-reader."
  value       = module.platform_iam.dev_secrets_reader_role_arn
}