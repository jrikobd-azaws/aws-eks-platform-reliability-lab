variable "cluster_name" {
  description = "Cluster name used to identify the disposable database."
  type        = string
}

variable "db_instance_class" {
  description = "Regional-orderability-verified PostgreSQL instance class."
  type        = string
}

variable "db_engine_version" {
  description = "Exact PostgreSQL engine version verified in the target Region."
  type        = string
}

variable "db_name" {
  description = "Initial database name."
  type        = string
}

variable "db_master_username" {
  description = "Master username; RDS manages the generated password in Secrets Manager."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for the RDS DB subnet group."
  type        = list(string)
}

variable "node_security_group_id" {
  description = "Only this EKS managed-node security group may connect to PostgreSQL."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID for the database security group."
  type        = string
}

variable "tags" {
  description = "Approved Project/Stack ownership tags."
  type        = map(string)
}