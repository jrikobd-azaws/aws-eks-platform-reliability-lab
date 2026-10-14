terraform {
  required_version = ">= 1.15.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.97"
    }
  }
}

variable "expected_account_id" {
  description = "Expected AWS account ID for the fixture Terraform root. Supply outside Git during human-approved initialization."
  type        = string
  default     = "azawslab-eks-fixture-275544662748-eu-west-1"
}

variable "fixture_bucket_name" {
  description = "Exact S3 bucket name for the disposable safety fixture. Supply outside Git during human-approved initialization."
  type        = string
  default     = "azawslab-eks-fixture-275544662748-eu-west-1"
}

provider "aws" {
  region              = "eu-west-1"
  allowed_account_ids = [var.expected_account_id]

  default_tags {
    tags = {
      Project   = "aws-eks-platform-reliability-lab"
      Stack     = "fixture"
      Owner     = "jrikobd-azaws"
      Purpose   = "disposable-safety-fixture"
      ManagedBy = "terraform"
    }
  }
}

resource "aws_s3_bucket" "fixture" {
  bucket = var.fixture_bucket_name

  tags = {
    Name    = "aws-eks-platform-reliability-lab-fixture"
    Project = "aws-eks-platform-reliability-lab"
    Stack   = "fixture"
    Purpose = "disposable-safety-fixture"
  }
}

resource "aws_s3_bucket_versioning" "fixture" {
  bucket = aws_s3_bucket.fixture.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "fixture" {
  bucket = aws_s3_bucket.fixture.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "fixture" {
  bucket = aws_s3_bucket.fixture.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "fixture_tls_only" {
  bucket = aws_s3_bucket.fixture.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "DenyInsecureTransport"
      Effect = "Deny"
      Principal = {
        AWS = "*"
      }
      Action = "s3:*"
      Resource = [
        "${aws_s3_bucket.fixture.arn}",
        "${aws_s3_bucket.fixture.arn}/*",
      ]
      Condition = {
        Bool = {
          "aws:SecureTransport" = "false"
        }
      }
    }]
  })
}
