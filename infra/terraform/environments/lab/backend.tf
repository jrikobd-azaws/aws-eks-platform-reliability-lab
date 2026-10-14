terraform {
  backend "s3" {
    key          = "lab/terraform.tfstate"
    region       = "eu-west-1"
    encrypt      = true
    use_lockfile = true
  }
}