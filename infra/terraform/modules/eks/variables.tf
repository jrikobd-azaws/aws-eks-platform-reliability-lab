variable "cluster_name" {
  description = "EKS cluster name."
  type        = string
}

variable "cluster_version" {
  description = "Approved Kubernetes version, re-verified in the target Region before provisioning."
  type        = string
}

variable "cluster_admin_principal_arn" {
  description = "IAM user or role principal granted cluster-admin access through an EKS access entry."
  type        = string
}

variable "cluster_endpoint_public_access_cidrs" {
  description = "Restricted IPv4 CIDRs allowed to reach the public Kubernetes API endpoint."
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for the EKS control plane ENIs and managed nodes."
  type        = list(string)
}

variable "vpc_id" {
  description = "VPC ID for EKS security groups."
  type        = string
}

variable "node_group_min_size" {
  description = "Minimum managed node count."
  type        = number
}

variable "node_group_desired_size" {
  description = "Desired managed node count."
  type        = number
}

variable "node_group_max_size" {
  description = "Maximum managed node count."
  type        = number
}

variable "tags" {
  description = "Approved Project/Stack ownership tags."
  type        = map(string)
}