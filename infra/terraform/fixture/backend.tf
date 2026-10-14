terraform {
  backend "s3" {
    key          = "fixture/terraform.tfstate"
    region       = "eu-west-1"
    encrypt      = true
    use_lockfile = true
  }
}
