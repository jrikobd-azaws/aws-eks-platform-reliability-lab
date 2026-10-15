
resource "aws_iam_role_policy" "fastapi_auth_reader" {
  name = "fastapi-auth-secret-reader"
  role = aws_iam_role.dev_secrets_reader.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = var.fastapi_auth_secret_arn
    }]
  })
}
