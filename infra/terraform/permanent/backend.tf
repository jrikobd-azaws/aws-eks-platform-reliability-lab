terraform {
  backend "s3" {
    key          = "permanent/terraform.tfstate"
    region       = "eu-west-1"
    encrypt      = true
    use_lockfile = true
  }
}
