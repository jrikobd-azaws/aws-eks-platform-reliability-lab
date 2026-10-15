variable "expected_account_id" {
  description = "Exact AWS account ID expected for the disposable lab stack. Supply outside Git."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "expected_account_id must be a 12-digit AWS account ID."
  }
}

variable "vpc_cidr" {
  description = "Non-overlapping lab VPC CIDR; verify it against all connected networks before provisioning."
  type        = string
  default     = "10.30.0.0/16"

  validation {
    condition     = can(cidrsubnet(var.vpc_cidr, 4, 3))
    error_message = "vpc_cidr must leave room for four /20-or-smaller lab subnets."
  }
}

variable "cluster_name" {
  description = "Exact disposable EKS cluster name."
  type        = string
  default     = "eks-platform-reliability-lab"
}

variable "cluster_version" {
  description = "Approved Kubernetes version. Verify regional EKS support and add-on compatibility before provisioning."
  type        = string
  default     = "1.36"

  validation {
    condition     = contains(["1.36", "1.37"], var.cluster_version)
    error_message = "Only the approved 1.36 baseline and planned 1.37 upgrade target are permitted."
  }
}

variable "cluster_admin_principal_arn" {
  description = "Explicit IAM user or role ARN in the expected account to grant EKS cluster-admin access."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:iam::${var.expected_account_id}:(user|role)/.+$", var.cluster_admin_principal_arn))
    error_message = "cluster_admin_principal_arn must be an IAM user or role ARN in expected_account_id."
  }
}

variable "cluster_endpoint_public_access_cidrs" {
  description = "Explicit trusted IPv4 CIDRs for the EKS public API endpoint. Do not use 0.0.0.0/0."
  type        = list(string)

  validation {
    condition = length(var.cluster_endpoint_public_access_cidrs) > 0 && alltrue([
      for cidr in var.cluster_endpoint_public_access_cidrs : can(cidrnetmask(cidr)) && tonumber(split("/", cidr)[1]) > 0
    ])
    error_message = "Provide at least one valid, restricted IPv4 CIDR; 0.0.0.0/0 is prohibited."
  }
}

variable "node_group_min_size" {
  description = "Minimum managed node group size. The approved baseline is two nodes."
  type        = number
  default     = 2
}

variable "node_group_desired_size" {
  description = "Desired managed node group size. The approved baseline is two nodes."
  type        = number
  default     = 2
}

variable "node_group_max_size" {
  description = "Maximum managed node group size; keep the cap intentional to control cost."
  type        = number
  default     = 2

  validation {
    condition     = var.node_group_min_size >= 1 && var.node_group_min_size <= var.node_group_desired_size && var.node_group_desired_size <= var.node_group_max_size
    error_message = "Node group sizes must satisfy 1 <= min_size <= desired_size <= max_size."
  }
}

variable "db_instance_class" {
  description = "Small PostgreSQL class; confirm regional orderability and quota before provisioning."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_engine_version" {
  description = "Exact PostgreSQL minor version verified as orderable in eu-west-1 before provisioning."
  type        = string
}

variable "db_name" {
  description = "Initial PostgreSQL database name."
  type        = string
  default     = "app"
}

variable "db_master_username" {
  description = "RDS master username; the password is generated and managed by RDS."
  type        = string
  default     = "lab_admin"
}
variable "app_runtime_secret_arn" {
  description = "Exact Secrets Manager ARN for the dedicated PostgreSQL application runtime identity."
  type        = string

  validation {
    condition = can(regex(
      "^arn:aws:secretsmanager:eu-west-1:${var.expected_account_id}:secret:eks-platform-reliability-lab/dev/app-runtime-[A-Za-z0-9]{6}$",
      var.app_runtime_secret_arn
    ))
    error_message = "Provide the exact application runtime secret ARN in the approved AWS account."
  }
}
