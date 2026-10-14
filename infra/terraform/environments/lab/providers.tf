terraform {
  required_version = ">= 1.15.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.97"
    }
  }
}

provider "aws" {
  region = "eu-west-1"

  default_tags {
    tags = {
      Project   = "aws-eks-platform-reliability-lab"
      Stack     = "lab"
      Owner     = "jrikobd-azaws"
      ManagedBy = "terraform"
    }
  }
}
