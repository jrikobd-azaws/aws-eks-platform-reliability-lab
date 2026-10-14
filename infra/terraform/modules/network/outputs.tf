output "vpc_id" {
  description = "Lab VPC ID."
  value       = aws_vpc.this.id
}

output "public_subnet_ids" {
  description = "Public subnet IDs ordered by the selected Availability Zones."
  value       = [for az in var.availability_zones : aws_subnet.public[az].id]
}

output "private_subnet_ids" {
  description = "Private subnet IDs ordered by the selected Availability Zones."
  value       = [for az in var.availability_zones : aws_subnet.private[az].id]
}