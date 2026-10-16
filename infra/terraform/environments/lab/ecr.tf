# Stage 3 - Application container repositories
# Managed by Terraform; image publication is a separate operation.

resource "aws_ecr_repository" "stage3" {
  for_each = toset(["backend", "frontend"])

  name                 = "eks-platform-reliability-lab/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = {
    Project   = "eks-platform-reliability-lab"
    Component = each.key
    ManagedBy = "Terraform"
  }
}

output "stage3_ecr_repository_urls" {
  description = "ECR repository URLs for Stage 3 application images."
  value = {
    for name, repo in aws_ecr_repository.stage3 :
    name => repo.repository_url
  }
}
