variable "expected_account_id" {
  description = "Expected AWS account ID used to construct the cluster OIDC provider ARN."
  type        = string
}

variable "cluster_name" {
  description = "Existing EKS cluster name used in IAM role and policy names."
  type        = string
}

variable "cluster_oidc_issuer_url" {
  description = "OIDC issuer URL exported by the existing EKS cluster."
  type        = string
}

variable "rds_master_secret_arn" {
  description = "Exact RDS-managed secret ARN authorized for the dev External Secrets service account."
  type        = string
}

variable "tags" {
  description = "Approved Project/Stack ownership tags."
  type        = map(string)
}