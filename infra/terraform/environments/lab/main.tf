data "aws_availability_zones" "available" {
  state = "available"
}

check "two_availability_zones" {
  assert {
    condition     = length(data.aws_availability_zones.available.names) >= 2
    error_message = "The lab VPC requires at least two available eu-west-1 Availability Zones."
  }
}

locals {
  availability_zones = slice(data.aws_availability_zones.available.names, 0, 2)
  lab_tags = {
    Project   = "aws-eks-platform-reliability-lab"
    Stack     = "lab"
    Owner     = "jrikobd-azaws"
    Purpose   = "disposable-lab-infrastructure"
    ManagedBy = "terraform"
  }
}

module "network" {
  source = "../../modules/network"

  cluster_name       = var.cluster_name
  vpc_cidr           = var.vpc_cidr
  availability_zones = local.availability_zones
  tags               = local.lab_tags
}

module "eks" {
  source = "../../modules/eks"

  cluster_name                         = var.cluster_name
  cluster_version                      = var.cluster_version
  cluster_admin_principal_arn          = var.cluster_admin_principal_arn
  cluster_endpoint_public_access_cidrs = var.cluster_endpoint_public_access_cidrs
  private_subnet_ids                   = module.network.private_subnet_ids
  vpc_id                               = module.network.vpc_id
  node_group_min_size                  = var.node_group_min_size
  node_group_desired_size              = var.node_group_desired_size
  node_group_max_size                  = var.node_group_max_size
  tags                                 = local.lab_tags
}

module "rds" {
  source = "../../modules/rds"

  cluster_name           = var.cluster_name
  db_instance_class      = var.db_instance_class
  db_engine_version      = var.db_engine_version
  db_name                = var.db_name
  db_master_username     = var.db_master_username
  private_subnet_ids     = module.network.private_subnet_ids
  node_security_group_id = module.eks.node_security_group_id
  vpc_id                 = module.network.vpc_id
  tags                   = local.lab_tags
}