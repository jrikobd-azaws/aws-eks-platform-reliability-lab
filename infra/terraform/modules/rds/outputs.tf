output "address" {
  description = "Private PostgreSQL endpoint hostname."
  value       = aws_db_instance.this.address
}

output "port" {
  description = "PostgreSQL listener port."
  value       = aws_db_instance.this.port
}

output "database_name" {
  description = "Initial database name."
  value       = aws_db_instance.this.db_name
}

output "master_secret_arn" {
  description = "RDS-managed master credential secret ARN; no password value is exposed."
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}