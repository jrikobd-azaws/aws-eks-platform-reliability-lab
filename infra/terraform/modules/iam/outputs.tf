output "aws_load_balancer_controller_role_arn" {
  description = "IRSA role ARN for kube-system/aws-load-balancer-controller."
  value       = aws_iam_role.aws_load_balancer_controller.arn
}

output "dev_secrets_reader_role_arn" {
  description = "IRSA role ARN for dev/external-secrets-reader."
  value       = aws_iam_role.dev_secrets_reader.arn
}