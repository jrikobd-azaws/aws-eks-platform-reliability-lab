variable "terraform_state_bucket" {
  description = "Exact name of the human-managed S3 state bucket for Terraform backend and lock files."
  type        = string

  validation {
    condition     = length(trimspace(var.terraform_state_bucket)) > 0 && can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.terraform_state_bucket))
    error_message = "terraform_state_bucket must be the exact S3 bucket name supplied outside Git."
  }
}

locals {
  github_repo = "jrikobd-azaws/aws-eks-platform-reliability-lab"

  trusted_plan_subjects = [
    "repo:${local.github_repo}:ref:refs/heads/main",
  ]
}

data "aws_iam_openid_connect_provider" "github_actions" {
  arn = "arn:aws:iam::${var.expected_account_id}:oidc-provider/token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "github_terraform_plan_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github_actions.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.trusted_plan_subjects
    }
  }
}

data "aws_iam_policy_document" "github_terraform_plan_permissions" {
  statement {
    sid    = "TerraformStateReadOnly"
    effect = "Allow"
    actions = [
      "s3:GetBucketLocation",
      "s3:GetBucketVersioning",
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:ListBucket",
    ]
    resources = [
      "arn:aws:s3:::${var.terraform_state_bucket}",
      "arn:aws:s3:::${var.terraform_state_bucket}/*",
    ]
  }

  statement {
    sid    = "TerraformStateLockFiles"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = [
      "arn:aws:s3:::${var.terraform_state_bucket}/*.tflock",
    ]
  }

  statement {
    sid    = "AccountReadOnly"
    effect = "Allow"
    actions = [
      "budgets:DescribeBudget",
      "budgets:DescribeBudgets",
      "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "github_terraform_plan" {
  name                 = "github-actions-terraform-plan"
  assume_role_policy   = data.aws_iam_policy_document.github_terraform_plan_assume_role.json
  max_session_duration = 3600

  tags = {
    Name    = "github-actions-terraform-plan"
    Project = "aws-eks-platform-reliability-lab"
    Stack   = "permanent"
  }
}

resource "aws_iam_policy" "github_terraform_plan" {
  name        = "github-actions-terraform-plan"
  description = "Scoped read-only permissions for human-approved Terraform planning in GitHub Actions."
  policy      = data.aws_iam_policy_document.github_terraform_plan_permissions.json
}

resource "aws_iam_role_policy_attachment" "github_terraform_plan" {
  role       = aws_iam_role.github_terraform_plan.name
  policy_arn = aws_iam_policy.github_terraform_plan.arn
}