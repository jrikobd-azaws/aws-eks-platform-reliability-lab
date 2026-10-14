variable "expected_account_id" {
  description = "Expected AWS account ID for this Terraform root. Supply outside Git during human-approved initialization."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "expected_account_id must be a 12-digit AWS account ID."
  }
}

provider "aws" {
  region              = "eu-west-1"
  allowed_account_ids = [var.expected_account_id]

  default_tags {
    tags = {
      Project   = "aws-eks-platform-reliability-lab"
      Stack     = "permanent"
      Owner     = "jrikobd-azaws"
      Purpose   = "permanent-foundation"
      ManagedBy = "terraform"
    }
  }
}
