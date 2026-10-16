data "aws_secretsmanager_secret" "fastapi_auth" {
  name = "eks-platform-reliability-lab/dev/fastapi-auth"
}
