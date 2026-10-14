# Terraform S3 bootstrap runbook

This runbook is the human-approved manual bootstrap for the Terraform state backend. It is intentionally local-only and does not create live AWS resources during agent execution.

## 0. Required bootstrap preflight

Before any bucket write, the human operator must confirm all of the following:

- AWS account is the approved account for this lab
- Region is `eu-west-1`
- Identity is a non-root IAM principal named `azawslab-admin`
- No unapproved `terraform apply` or GitHub push is in progress

```bash
aws sts get-caller-identity --output json
aws configure get region
aws iam get-user --user-name azawslab-admin
```

A bootstrap write must stop immediately if the account, Region, or identity does not match the approved lab configuration.

## 1. Create the S3 bucket for Terraform state

Set the bucket name explicitly in a shell variable before any write.

```bash
TF_STATE_BUCKET="aws-eks-platform-reliability-lab-tfstate"
aws s3api create-bucket \
  --bucket "$TF_STATE_BUCKET" \
  --region eu-west-1 \
  --create-bucket-configuration LocationConstraint=eu-west-1
```

If the bucket already exists, do not recreate it.

## 2. Enable versioning

```bash
aws s3api put-bucket-versioning \
  --bucket "$TF_STATE_BUCKET" \
  --versioning-configuration Status=Enabled \
  --region eu-west-1
```

## 3. Enable server-side encryption

```bash
aws s3api put-bucket-encryption \
  --bucket "$TF_STATE_BUCKET" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' \
  --region eu-west-1
```

## 4. Block public access

```bash
aws s3api put-public-access-block \
  --bucket "$TF_STATE_BUCKET" \
  --public-access-block-configuration '{"BlockPublicAcls":true,"IgnorePublicAcls":true,"BlockPublicPolicy":true,"RestrictPublicBuckets":true}' \
  --region eu-west-1
```

## 5. Enforce TLS for the bucket without overwriting existing bucket policy

Read the current policy first. If it exists, append only the TLS deny statement. Do not overwrite a policy that is already in place without explicit review.

```bash
aws s3api get-bucket-policy --bucket "$TF_STATE_BUCKET" --region eu-west-1 || echo "No bucket policy present"
```

```bash
aws s3api put-bucket-policy \
  --bucket "$TF_STATE_BUCKET" \
  --policy '{
    "Version":"2012-10-17",
    "Statement":[{
      "Sid":"DenyInsecureTransport",
      "Effect":"Deny",
      "Principal":"*",
      "Action":"s3:*",
      "Resource":[
        "arn:aws:s3:::aws-eks-platform-reliability-lab-tfstate",
        "arn:aws:s3:::aws-eks-platform-reliability-lab-tfstate/*"
      ],
      "Condition":{"Bool":{"aws:SecureTransport":"false"}}
    }]
  }' \
  --region eu-west-1
```

If a bucket policy already exists, review it first and do not replace it blindly.

## 6. Read-only verification commands

Use these commands to verify the backend state bucket configuration without modifying anything.

```bash
aws s3api get-bucket-versioning --bucket "$TF_STATE_BUCKET" --region eu-west-1
aws s3api get-bucket-encryption --bucket "$TF_STATE_BUCKET" --region eu-west-1
aws s3api get-public-access-block --bucket "$TF_STATE_BUCKET" --region eu-west-1
aws s3api get-bucket-acl --bucket "$TF_STATE_BUCKET" --region eu-west-1
aws s3api get-bucket-policy --bucket "$TF_STATE_BUCKET" --region eu-west-1 || echo "No bucket policy present"
```

Ownership and TLS policy checks should be reviewed by the human operator before any further write.

## 7. Initialize each Terraform root with the exact bucket name

For the permanent root:

```bash
TF_STATE_BUCKET="aws-eks-platform-reliability-lab-tfstate"
terraform -chdir=infra/terraform/permanent init \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="key=permanent/terraform.tfstate" \
  -backend-config="region=eu-west-1" \
  -backend-config="encrypt=true" \
  -backend-config="use_lockfile=true"
```

For the fixture root:

```bash
TF_STATE_BUCKET="aws-eks-platform-reliability-lab-tfstate"
terraform -chdir=infra/terraform/fixture init \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="key=fixture/terraform.tfstate" \
  -backend-config="region=eu-west-1" \
  -backend-config="encrypt=true" \
  -backend-config="use_lockfile=true"
```

## 8. Human-controlled apply gate

This runbook does not approve any AWS write. The actual `terraform apply` and infrastructure lifecycle remain human-approved only. No agent may execute them without explicit approval.
