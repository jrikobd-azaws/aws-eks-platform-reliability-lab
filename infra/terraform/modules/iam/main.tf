locals {
  oidc_issuer_hostpath = trimprefix(var.cluster_oidc_issuer_url, "https://")
  oidc_provider_arn    = "arn:aws:iam::${var.expected_account_id}:oidc-provider/${local.oidc_issuer_hostpath}"
}

resource "aws_iam_policy" "aws_load_balancer_controller" {
  name        = "${var.cluster_name}-aws-load-balancer-controller"
  description = "AWS Load Balancer Controller v3.6.0 policy with disabled WAF and Shield permissions removed."
  policy      = file("${path.module}/aws-lbc-iam-policy.json")
  tags        = var.tags
}

resource "aws_iam_role" "aws_load_balancer_controller" {
  name = "${var.cluster_name}-aws-load-balancer-controller"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = local.oidc_provider_arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_issuer_hostpath}:aud" = "sts.amazonaws.com"
          "${local.oidc_issuer_hostpath}:sub" = "system:serviceaccount:kube-system:aws-load-balancer-controller"
        }
      }
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "aws_load_balancer_controller" {
  role       = aws_iam_role.aws_load_balancer_controller.name
  policy_arn = aws_iam_policy.aws_load_balancer_controller.arn
}

resource "aws_iam_role" "dev_secrets_reader" {
  name = "${var.cluster_name}-dev-secrets-reader"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = local.oidc_provider_arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_issuer_hostpath}:aud" = "sts.amazonaws.com"
          "${local.oidc_issuer_hostpath}:sub" = "system:serviceaccount:dev:external-secrets-reader"
        }
      }
    }]
  })

  tags = var.tags
}

resource "aws_iam_policy" "dev_secrets_reader" {
  name        = "${var.cluster_name}-dev-secrets-reader"
  description = "Read only the RDS-managed master secret used by the dev namespace."
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "secretsmanager:DescribeSecret",
        "secretsmanager:GetSecretValue",
      ]
      Resource = var.app_runtime_secret_arn
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "dev_secrets_reader" {
  role       = aws_iam_role.dev_secrets_reader.name
  policy_arn = aws_iam_policy.dev_secrets_reader.arn
}