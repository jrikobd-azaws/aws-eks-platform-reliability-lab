output "vpc_id" {
  description = "Disposable lab VPC ID."
  value       = module.network.vpc_id
}

output "public_subnet_ids" {
  description = "Public subnet IDs for internet-facing load balancers and the single NAT gateway."
  value       = module.network.public_subnet_ids
}

output "private_subnet_ids" {
  description = "Private subnet IDs for EKS nodes and RDS."
  value       = module.network.private_subnet_ids
}

output "cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "cluster_arn" {
  description = "EKS cluster ARN for exact identity verification."
  value       = module.eks.cluster_arn
}

output "cluster_endpoint" {
  description = "EKS Kubernetes API endpoint."
  value       = module.eks.cluster_endpoint
}

output "cluster_oidc_issuer_url" {
  description = "EKS OIDC issuer URL used by workload IAM roles."
  value       = module.eks.cluster_oidc_issuer_url
}

output "node_group_name" {
  description = "EKS managed node group name."
  value       = module.eks.node_group_name
}

output "rds_address" {
  description = "Private RDS endpoint hostname."
  value       = module.rds.address
}

output "rds_port" {
  description = "Private RDS PostgreSQL port."
  value       = module.rds.port
}

output "rds_database_name" {
  description = "Initial PostgreSQL database name."
  value       = module.rds.database_name
}

output "rds_master_secret_arn" {
  description = "RDS-managed master credential secret ARN; secret value is never output."
  value       = module.rds.master_secret_arn
}