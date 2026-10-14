terraform {
  required_version = ">= 1.15.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.97"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

provider "aws" {
  region              = "eu-west-1"
  allowed_account_ids = [var.expected_account_id]

  default_tags {
    tags = {
      Project   = "aws-eks-platform-reliability-lab"
      Stack     = "lab"
      Owner     = "jrikobd-azaws"
      Purpose   = "disposable-lab-infrastructure"
      ManagedBy = "terraform"
    }
  }
}
