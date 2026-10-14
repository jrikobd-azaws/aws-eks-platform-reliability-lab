variable "cluster_name" {
  description = "EKS cluster name used for subnet discovery tags."
  type        = string
}

variable "vpc_cidr" {
  description = "IPv4 CIDR for the lab VPC."
  type        = string
}

variable "availability_zones" {
  description = "Exactly two available Availability Zones."
  type        = list(string)

  validation {
    condition     = length(var.availability_zones) == 2
    error_message = "The lab network must span exactly two Availability Zones."
  }
}

variable "tags" {
  description = "Approved Project/Stack ownership tags."
  type        = map(string)
}