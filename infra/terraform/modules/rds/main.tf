data "aws_rds_orderable_db_instance" "postgres" {
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class
  storage_type   = "gp3"
  vpc            = true
}

resource "aws_db_subnet_group" "this" {
  name       = "${var.cluster_name}-db"
  subnet_ids = var.private_subnet_ids

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-db"
  })
}

resource "aws_security_group" "this" {
  name        = "${var.cluster_name}-rds"
  description = "Private PostgreSQL access from the EKS managed node group only."
  vpc_id      = var.vpc_id

  ingress {
    description     = "PostgreSQL from the EKS managed node group."
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [var.node_security_group_id]
  }

  egress {
    description = "Allow stateful database response traffic."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-rds"
  })
}

resource "aws_db_instance" "this" {
  identifier                   = "${var.cluster_name}-postgres"
  engine                       = "postgres"
  engine_version               = data.aws_rds_orderable_db_instance.postgres.engine_version
  instance_class               = data.aws_rds_orderable_db_instance.postgres.instance_class
  allocated_storage            = 20
  storage_type                 = "gp3"
  storage_encrypted            = true
  db_name                      = var.db_name
  username                     = var.db_master_username
  manage_master_user_password  = true
  port                         = 5432
  db_subnet_group_name         = aws_db_subnet_group.this.name
  vpc_security_group_ids       = [aws_security_group.this.id]
  publicly_accessible          = false
  multi_az                     = false
  backup_retention_period      = 1
  auto_minor_version_upgrade   = true
  deletion_protection          = false
  skip_final_snapshot          = true
  delete_automated_backups     = true
  apply_immediately            = false
  performance_insights_enabled = false
  monitoring_interval          = 0

  depends_on = [data.aws_rds_orderable_db_instance.postgres]

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-postgres"
  })
}